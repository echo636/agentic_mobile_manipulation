"""Small shared lifecycle helpers for task outcomes and cooperative GPU leases."""
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import time


def preferred_gpu_worker(workers, states, host, gpu_csv):
    """Choose the most free eligible GPU from the existing admission snapshot.

    Shared lab jobs can grow after admission. Prefer available headroom rather
    than whichever worker thread wins the host lease; never claim another GPU
    here or touch external processes. Busy lanes and known occupied ports are
    excluded so preference cannot block independent lanes on that host.
    """
    memory = {}
    for line in gpu_csv.splitlines():
        fields = [v.strip() for v in line.split(',')]
        try:
            memory[int(fields[0])] = (int(fields[3]) - int(fields[2]), int(fields[2]))
        except (ValueError, IndexError):
            continue
    busy = {'starting', 'controller_starting', 'running', 'cleanup_pending', 'disabled', 'drained'}
    choices = []
    for worker in workers:
        if worker['ssh'][-1] != host or worker['gpu'] not in memory:
            continue
        state = states.get(worker['id'], {})
        if state.get('stage') in busy or state.get('checks', {}).get('bridge_port_free') is False:
            continue
        free, used = memory[worker['gpu']]
        minimum = worker.get('minimum_free_gpu_mib', 0)
        if (minimum and free < minimum) or (not minimum and used >= 1024):
            continue
        choices.append((free, -worker['gpu'], worker['id']))
    return max(choices)[2] if choices else None


def classify_episode_outcome(row):
    """Task outcome is independent of optional replay/media validation.

    Historical callers can supply ``controller_timeout`` from the original
    controller metadata. A missing score alone never proves a timeout.
    """
    if row.get('status') not in {'passed', 'failed', 'blocked'} and not row.get('execution_finished_at'):
        return None
    if row.get('startup_timed_out') or row.get('termination_reason')=='startup_timeout':
        return 'failure'
    if row.get('budget_basis')=='model_execution_excludes_initialization' and not isinstance(row.get('execution_started_at_unix'),(int,float)):
        return 'failure'
    finished_in_time=(row.get('deadline_expired_at_finish') is False and
                      isinstance(row.get('execution_finished_at_unix'),(int,float)))
    if row.get('deadline_expired_at_finish') is True:
        return 'timeout'
    if not finished_in_time and (row.get('episode_outcome') == 'timeout' or row.get('episode_timed_out')
            or row.get('controller_timeout') or row.get('timeout')
            or row.get('termination_reason') == 'episode_deadline_exceeded'):
        return 'timeout'
    if row.get('episode_outcome') in {'success', 'failure'}:
        return row['episode_outcome']
    if (row.get('task_success') is True and (finished_in_time or
            (row.get('controller_status', 'passed') == 'passed' and not row.get('failure')))):
        return 'success'
    return 'failure'


def validate_manifest_coverage(rows, expected_count=None, expected_tasks=None):
    """Check identities only; never create outcomes for tasks that never ran."""
    if expected_count is not None and len(rows)!=expected_count:
        raise ValueError(f'Manifest coverage mismatch: expected {expected_count}, found {len(rows)}')
    indices=[row['index'] for row in rows]
    run_ids=[row['run_id'] for row in rows]
    if len(set(indices))!=len(indices) or len(set(run_ids))!=len(run_ids):
        raise ValueError('Manifest contains duplicate task indices or run IDs')
    if expected_tasks is not None:
        actual={row['index']:row['task'] for row in rows}
        if actual!=expected_tasks:raise ValueError('Saved records changed frozen manifest task identities')


def require_completed_scope(rows, *, draining=False):
    if draining:return
    pending=[row.get('run_id',str(row.get('index'))) for row in rows
             if row.get('status') not in {'passed','failed','blocked'}]
    if pending:
        raise RuntimeError('Incomplete batch coverage: '+str(len(pending))+
                           ' tasks remain nonterminal; resume existing records: '+', '.join(pending[:10]))


class FileLease:
    """An advisory cross-process flock; never unlink the shared lock inode."""
    def __init__(self, path, owner):
        self.path, self.owner = Path(path), owner
        self.metadata_path=self.path.with_suffix('.json')
        self.stream = None
        self.previous_owner = {}

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open('a+')
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            stream.close()
            return False
        self.stream = stream
        stream.seek(0)
        try:
            raw=self.metadata_path.read_text() if self.metadata_path.exists() else stream.read()
            self.previous_owner = json.loads(raw) if raw.strip() else {}
        except (ValueError, OSError):
            self.release()
            raise RuntimeError('Lease owner metadata unreadable; refusing unknown slot ownership')
        return True

    def record(self, **extra):
        self.owner.update(extra)
        temporary=self.metadata_path.with_suffix('.json.tmp.'+str(os.getpid()))
        temporary.write_text(json.dumps({**self.owner, 'lease_pid': os.getpid(), 'updated_unix': time.time()}))
        temporary.replace(self.metadata_path)

    def release(self, *, cleared=False):
        if self.stream is None:
            return
        try:
            if cleared:self.record(released=True)
        finally:
            fcntl.flock(self.stream, fcntl.LOCK_UN)
            self.stream.close()
            self.stream = None


class WorkerLease:
    """Reserve one account/host capacity slot and one physical GPU.

    All methods must share the directory and host slot sizing. Host capacity is
    independent of the number of candidate GPU workers configured by an arm.
    """
    def __init__(self, directory, worker, *, host_budget_gib, slot_memory_gib=28, owner=None):
        self.directory = Path(directory)
        self.worker = worker
        self.owner = dict(owner or {}, worker_id=worker['id'], host=worker['ssh'][-1],
                          gpu=worker['gpu'], gpu_uuid=worker.get('expected_gpu_uuid'))
        if worker.get('memory_budget_gib', 28) > slot_memory_gib:
            raise ValueError('Worker memory exceeds shared host slot size')
        self.slot_count = math.floor(host_budget_gib / slot_memory_gib)
        if self.slot_count < 1:
            raise ValueError('Host budget has no usable simulator slot')
        self.host_key = hashlib.sha256(worker['ssh'][-1].encode()).hexdigest()[:20]
        device = worker.get('expected_gpu_uuid') or worker['ssh'][-1] + ':' + str(worker['gpu'])
        self.gpu_key = hashlib.sha256(device.encode()).hexdigest()[:24]
        self.host = self.gpu = None

    def acquire(self):
        gpu = FileLease(self.directory / ('gpu-' + self.gpu_key + '.lock'), self.owner.copy())
        if not gpu.acquire():
            return False
        try:
            for slot in range(self.slot_count):
                host = FileLease(self.directory / f'host-{self.host_key}-{slot}.lock', self.owner.copy())
                if host.acquire():
                    self.gpu, self.host = gpu, host
                    return True
        except BaseException:
            gpu.release()
            raise
        gpu.release()
        return False

    @property
    def previous_owners(self):
        return [lease.previous_owner for lease in (self.gpu, self.host) if lease]

    def record(self, **details):
        for lease in (self.gpu, self.host):
            if lease:
                lease.record(**details)

    def other_reserved_gib(self, slot_memory_gib=28):
        reserved=0
        for slot in range(self.slot_count):
            path=self.directory / f'host-{self.host_key}-{slot}.lock'
            if self.host and path==self.host.path:continue
            with path.open('a+') as stream:
                try:fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:reserved+=slot_memory_gib
                else:fcntl.flock(stream,fcntl.LOCK_UN)
        return reserved

    def release(self, *, cleared=False):
        try:
            if self.gpu:self.gpu.release(cleared=cleared)
        finally:
            try:
                if self.host:self.host.release(cleared=cleared)
            finally:self.gpu=self.host=None
