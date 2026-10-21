"""Measured host memory admission; simulator hard limits remain independent."""
import math
import os
from pathlib import Path
import subprocess

GIB = 1024**3


def working_bytes(current, stats):
    reclaimable = max(0, stats.get('file', 0) - stats.get('shmem', 0)
                      - stats.get('file_dirty', 0) - stats.get('file_writeback', 0))
    reclaimable += stats.get('slab_reclaimable', 0)
    return max(0, current - reclaimable)


def admission_decision(account_current, account_stats, account_limit, peers, *,
                       host_budget_gib, candidate_gib=28):
    """Reserve growth above live working memory, including every legacy peer.

    Completed initialization is necessary to use an observed peak. Missing
    telemetry and old coordinators retain the full hard-limit reservation.
    This is measured overcommit, not a proof that later peaks cannot grow.
    """
    if not 12 <= candidate_gib <= 28 or host_budget_gib <= 0:
        raise ValueError('Invalid adaptive memory budget')
    limit = host_budget_gib * GIB
    if account_limit is not None:
        limit = min(limit, account_limit)
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
        peak = peer.get('peak_bytes')
        measured = (peer.get('memory_admission_mode') == 'observed_peak'
                    and peer.get('runtime_stage') == 'ready' and isinstance(peak, int) and peak > 0)
        reserved = min(hard, max(12 * GIB, math.ceil(1.2 * peak + 2 * GIB))) if measured else hard
        reserved = max(reserved, current)
        growth += max(0, reserved - current)
        reservations.append({**peer, 'reserved_bytes': reserved,
                             'basis': 'observed_peak_with_margin' if measured else 'hard_limit_fallback'})
    projected = max(base, peer_current) + growth + candidate_gib * GIB
    return {'status': 'passed' if projected <= limit else 'blocked',
            'account_working_bytes': base, 'peer_growth_reserve_bytes': growth,
            'candidate_reserve_bytes': candidate_gib * GIB,
            'projected_bytes': projected, 'usable_budget_bytes': limit,
            'peers': reservations,
            'policy': 'ready_peak_1.2_plus_2GiB_min12; unknown/startup=hard_limit',
            'limitation': 'Observed peaks may grow later; no guarantee against aggregate OOM.'}


def read_peer_memory(peer, account_path):
    record = {k: peer[k] for k in ('unit', 'worker_id', 'runtime_stage', 'hard_limit_gib',
                                  'memory_admission_mode') if k in peer}
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
                              candidate_gib=policy['candidate_gib'])
