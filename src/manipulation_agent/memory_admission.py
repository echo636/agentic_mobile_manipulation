"""Measured host memory admission; simulator hard limits remain independent."""
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess

GIB = 1024**3
HIGH_UTILIZATION_POLICY_VERSION = 1


def working_bytes(current, stats):
    reclaimable = max(0, stats.get('file', 0) - stats.get('shmem', 0)
                      - stats.get('file_dirty', 0) - stats.get('file_writeback', 0))
    reclaimable += stats.get('slab_reclaimable', 0)
    return max(0, current - reclaimable)


def admission_decision(account_current, account_stats, account_limit, peers, *,
                       host_budget_gib, candidate_gib=28, startup_gib=None,
                       profile='observed_peak'):
    """Reserve growth above live working memory, including every legacy peer.

    Only sampled working-memory peaks count: memory.peak includes clean cache.
    Startup estimates are separate from per-process enforcement limits. Missing
    telemetry retains the hard-limit reservation. One account margin covers
    unobserved growth; this is not a guarantee against aggregate OOM.
    """
    if profile not in ('observed_peak', 'throughput'):
        raise ValueError('Invalid memory admission profile')
    if (not 12 <= candidate_gib <= 28
            or (host_budget_gib is None and profile != 'throughput')
            or (host_budget_gib is not None and (not math.isfinite(host_budget_gib) or host_budget_gib <= 0))):
        raise ValueError('Invalid adaptive memory budget')
    startup_gib = candidate_gib if startup_gib is None else startup_gib
    if not 12 <= startup_gib <= candidate_gib:
        raise ValueError('Invalid startup memory estimate')
    limit = None if host_budget_gib is None else host_budget_gib * GIB
    if account_limit is not None:
        account_budget = max(0, account_limit - 2 * GIB)
        limit = account_budget if limit is None else min(limit, account_budget)
    base = working_bytes(account_current, account_stats)
    growth = 0
    peer_current = 0
    reservations = []
    for peer in peers:
        hard = float(peer.get('hard_limit_gib', 28)) * GIB
        if not math.isfinite(hard) or hard <= 0:
            raise ValueError('Invalid peer memory limit')
        current = max(0, int(peer.get('working_bytes', 0)))
        peer_current += current
        peak = peer.get('working_peak_bytes')
        telemetry = (not peer.get('telemetry_error') and isinstance(peak, int) and peak > 0)
        measured = (peer.get('runtime_stage') == 'ready' and telemetry)
        basis = 'sampled_working_peak' if measured else 'hard_limit_fallback'
        reserved = min(hard, max(12 * GIB, math.ceil(1.1 * peak + GIB))) if measured else hard
        if profile == 'throughput' and telemetry and peer.get('runtime_stage') in ('starting', 'ready'):
            # Observe legacy peers too, without changing or restarting them.
            reserved = min(hard, math.ceil(1.1 * peak + GIB))
            basis = 'live_working_peak'
            if peer['runtime_stage'] == 'starting':
                estimate = float(peer.get('startup_memory_gib', startup_gib)) * GIB
                if not math.isfinite(estimate) or not 0 < estimate <= hard:
                    raise ValueError('Invalid peer startup estimate')
                reserved = max(reserved, estimate)
                basis = 'startup_estimate_and_live_peak'
        reserved = max(reserved, current)
        growth += max(0, reserved - current)
        reservations.append({**peer, 'reserved_bytes': reserved,
                             'basis': basis})
    projected = max(base, peer_current) + growth + startup_gib * GIB
    return {'status': 'passed' if limit is None or projected <= limit else 'blocked',
            'account_working_bytes': base, 'peer_growth_reserve_bytes': growth,
            'candidate_reserve_bytes': startup_gib * GIB,
            'candidate_hard_limit_bytes': candidate_gib * GIB,
            'projected_bytes': projected, 'usable_budget_bytes': limit,
            'peers': reservations,
            'profile': profile,
            'policy': ('throughput_v1; live=1.1*working_peak+1GiB; startup=max(estimate,live); account_margin=2GiB'
                       if profile == 'throughput' else
                       'working_peak_v2; ready=1.1*working_peak+1GiB_min12; startup=estimate; account_margin=2GiB'),
            'limitation': 'Observed peaks may grow later; no guarantee against aggregate OOM.'}


def sampled_working_peak(path, current):
    """Share working-memory samples across independent admission processes."""
    directory = Path('/tmp') / f'mas-memory-admission-{os.getuid()}'
    directory.mkdir(mode=0o700, exist_ok=True)
    identity = f'{path}:{path.stat().st_ino}'
    key = hashlib.sha256(identity.encode()).hexdigest()
    with (directory / (key + '.json')).open('a+') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.seek(0)
        try:
            previous = json.load(stream).get('working_peak_bytes', 0)
        except (ValueError, TypeError):
            previous = 0
        peak = max(current, int(previous))
        stream.seek(0); stream.truncate()
        json.dump({'working_peak_bytes': peak}, stream)
        return peak


def read_peer_memory(peer, account_path):
    record = {k: peer[k] for k in ('unit', 'worker_id', 'runtime_stage', 'hard_limit_gib',
                                  'memory_admission_mode', 'startup_memory_gib') if k in peer}
    if not peer.get('unit'):
        return record
    try:
        result = subprocess.run(['systemctl', '--user', 'show', '-p', 'ControlGroup', '--', peer['unit']], capture_output=True, text=True,
                                check=True, timeout=8)
        group = next(line.split('=', 1)[1] for line in result.stdout.splitlines()
                     if line.startswith('ControlGroup='))
        path = Path('/sys/fs/cgroup') / group.lstrip('/')
        if not group or account_path not in path.parents:
            raise ValueError('Missing or unexpected account cgroup')
        current = int((path / 'memory.current').read_text())
        stats = {k: int(v) for k, v in (line.split() for line in (path / 'memory.stat').read_text().splitlines())}
        record.update(working_bytes=working_bytes(current, stats), current_bytes=current)
        record['working_peak_bytes'] = sampled_working_peak(path, record['working_bytes'])
        peak = path / 'memory.peak'
        if peak.is_file():
            record['peak_bytes'] = int(peak.read_text())
    except (OSError, ValueError, StopIteration, subprocess.SubprocessError) as exc:
        record['telemetry_error'] = type(exc).__name__
    return record


def host_memory_admission(policy, *, account_current, account_stats, account_limit):
    account = Path('/sys/fs/cgroup/user.slice') / f'user-{os.getuid()}.slice'
    peers = [read_peer_memory(peer, account) for peer in policy['peers']]
    return admission_decision(account_current, account_stats, account_limit, peers,
                              host_budget_gib=policy['host_budget_gib'],
                              candidate_gib=policy['candidate_gib'],
                              startup_gib=policy.get('startup_gib'),
                              profile=policy.get('profile', 'observed_peak'))
