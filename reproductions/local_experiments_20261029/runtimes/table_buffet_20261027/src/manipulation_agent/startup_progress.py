"""Finite startup milestones; elapsed heartbeats never renew the watchdog."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time

STARTUP_POLICY = 'stage_progress_v1'


def startup_event(output, stage, status, **extra):
    at = time.time()
    event = {'stage': stage, 'status': status, 'at_unix': at,
             'at': datetime.fromtimestamp(at, timezone.utc).isoformat(), **extra}
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    with (output / 'startup_stages.jsonl').open('a') as stream:
        stream.write(json.dumps(event) + '\n')
    return event


@contextmanager
def startup_stage(output, name):
    started = time.monotonic()
    startup_event(output, name, 'running', elapsed_seconds=0.0)
    try:
        yield
    except BaseException as exc:
        startup_event(output, name, 'failed', elapsed_seconds=time.monotonic()-started,
                      error_type=type(exc).__name__, error=str(exc))
        raise
    else:
        startup_event(output, name, 'passed', elapsed_seconds=time.monotonic()-started)


def startup_progress_update(row, events, observed_at):
    """Pure transition: only fresh, unique milestones renew an unarmed attempt.

    Persisted legacy attempts keep their original deadline. A repeated milestone,
    heartbeat, malformed event, or event after a genuine idle expiry cannot keep
    a stuck constructor alive. Execution always takes over with its fixed clock.
    """
    if row.get('startup_watchdog_policy') != STARTUP_POLICY or row.get('episode_deadline_unix'):
        return {}
    deadline = row['startup_deadline_unix']
    began = row['simulator_started_at_unix']
    idle = row['startup_timeout_seconds']
    seen = set(row.get('startup_seen_milestones', []))
    latest = None
    for event in events:
        if not isinstance(event, dict):
            continue
        stage, status, at = event.get('stage'), event.get('status'), event.get('at_unix')
        if (not isinstance(stage, str) or not stage or status not in {'running', 'passed'}
                or isinstance(at, bool) or not isinstance(at, (int, float)) or not math.isfinite(at)):
            continue
        key = stage + ':' + status
        if key in seen or not began <= at <= min(observed_at + 5, deadline):
            continue
        seen.add(key)
        deadline = max(deadline, at + idle)
        if latest is None or at >= latest['at_unix']:
            latest = {'stage': stage, 'status': status, 'at_unix': at}
    if latest is None:
        return {}
    return {'startup_deadline_unix': deadline, 'startup_seen_milestones': sorted(seen),
            'startup_last_progress': latest}
