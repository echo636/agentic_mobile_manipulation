"""Finite startup milestones; elapsed heartbeats never renew the watchdog."""
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
import errno
import json
import math
from pathlib import Path
import sys
import time

STARTUP_POLICY = 'stage_progress_v2'


def startup_event(output, stage, status, **extra):
    at = time.time()
    event = {'stage': stage, 'status': status, 'at_unix': at,
             'at': datetime.fromtimestamp(at, timezone.utc).isoformat(), **extra}
    output = Path(output)
    # A transient NAS error in this observer must not replace the native
    # constructor's outcome. Retries retain the operation's original time;
    # they cannot act as watchdog heartbeats. Persistent failures stay visible
    # in the owned unit's journal without claiming a recorded milestone.
    for attempt in range(3):
        try:
            output.mkdir(parents=True, exist_ok=True)
            with (output / 'startup_stages.jsonl').open('a') as stream:
                # Separate a possible partial prior write before retrying.
                stream.write(('\n' if attempt else '') + json.dumps(event) + '\n')
            break
        except OSError as exc:
            if exc.errno not in {errno.EIO, errno.ESTALE, errno.ETIMEDOUT, errno.EAGAIN}:
                raise
            if attempt < 2:
                event['storage_retries'] = attempt + 1
                time.sleep(.5 * (attempt + 1))
                continue
            warning = {'event': 'startup_log_write_failed', 'errno': exc.errno,
                       'path': str(output / 'startup_stages.jsonl'), 'milestone': event}
            try:
                print(json.dumps(warning), file=sys.stderr, flush=True)
            except OSError:
                pass
    return event


@contextmanager
def startup_stage(output, name):
    started = time.monotonic()
    entered = startup_event(output, name, 'running', elapsed_seconds=0.0)
    try:
        yield
    except BaseException as exc:
        startup_event(output, name, 'failed', elapsed_seconds=time.monotonic()-started,
                      error_type=type(exc).__name__, error=str(exc))
        raise
    else:
        startup_event(output, name, 'passed', elapsed_seconds=time.monotonic()-started,
                      operation_started_at_unix=entered['at_unix'])


@contextmanager
def instrument_startup_calls(output, calls):
    """Temporarily report real constructor work without changing its outcome.

    Each key identifies a finite scene/object operation within its native call
    path. A completed invocation carries its matching entry time, so repeated
    native restoration passes count as real work. Polls and incomplete repeated
    entries are never progress. No simulator state is changed. Only use during
    single-threaded construction; restore all methods even if it fails.
    """
    originals = []
    scope = []
    try:
        for owner, name, key in calls:
            original = getattr(owner, name)
            owned = name in vars(owner)

            def instrument(original, key):
                @wraps(original)
                def wrapped(*args, **kwargs):
                    stage = key(*args, **kwargs)
                    if stage is None:
                        return original(*args, **kwargs)
                    # A later task reset may restore objects already visited by
                    # scene loading. Flat keys suppressed all of that genuine
                    # progress and falsely expired large scenes. Keep the finite
                    # parent call path, collapsing recursive repeats so recursion
                    # cannot manufacture a new watchdog key on every iteration.
                    qualified = '/'.join(dict.fromkeys((*scope, stage)))
                    scope.append(stage)
                    try:
                        with startup_stage(output, qualified):
                            return original(*args, **kwargs)
                    finally:
                        scope.pop()
                return wrapped

            setattr(owner, name, instrument(original, key))
            originals.append((owner, name, original, owned))
        yield
    finally:
        for owner, name, original, owned in reversed(originals):
            if owned:
                setattr(owner, name, original)
            else:
                delattr(owner, name)


def startup_progress_update(row, events, observed_at):
    """Pure transition: only fresh, unique milestones renew an unarmed attempt.

    Persisted legacy attempts keep their original deadline. Replayed records,
    heartbeats and incomplete repeated entries cannot renew it. A repeated native
    operation counts only after its matching entry and successful return; large
    scenes legitimately restore the same objects more than once.
    """
    if row.get('startup_watchdog_policy') != STARTUP_POLICY or row.get('episode_deadline_unix'):
        return {}
    deadline = row['startup_deadline_unix']
    began = row['simulator_started_at_unix']
    idle = row['startup_timeout_seconds']
    seen = set(row.get('startup_seen_milestones', []))
    latest = None
    entries = set()
    for event in events:
        if not isinstance(event, dict):
            continue
        stage, status, at = event.get('stage'), event.get('status'), event.get('at_unix')
        if (not isinstance(stage, str) or not stage or status not in {'running', 'passed'}
                or isinstance(at, bool) or not isinstance(at, (int, float)) or not math.isfinite(at)):
            continue
        if status == 'running':
            entries.add((stage, at))
        key = stage + ':' + status
        started = event.get('operation_started_at_unix')
        if (status == 'passed' and isinstance(started, (int, float))
                and not isinstance(started, bool) and began <= started <= at
                and (stage, started) in entries):
            key += ':' + str(started)
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
