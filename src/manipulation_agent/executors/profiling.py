"""Private, low-overhead component timings; never reported as model observations."""
from contextlib import contextmanager
from functools import wraps
import json
import time
from ..records import now

@contextmanager
def component(backend, name):
    start=time.perf_counter()
    try:yield
    finally:
        totals=getattr(backend,'_component_totals',None)
        if totals is None:totals=backend._component_totals={}
        row=totals.setdefault(name,{'seconds':0.,'calls':0})
        row['seconds']+=time.perf_counter()-start;row['calls']+=1

@contextmanager
def action_timing(backend, primitive):
    start = time.perf_counter()
    before = {k: dict(v) for k, v in getattr(backend, '_component_totals', {}).items()}
    status = 'failed'
    try:
        yield
        status = 'passed'
    finally:
        after = getattr(backend, '_component_totals', {})
        row = {'at': now(), 'primitive': primitive, 'status': status,
               'wall_seconds': time.perf_counter() - start,
               'components': {k: {field: v[field] - before.get(k, {}).get(field, 0)
                                  for field in v} for k, v in after.items()},
               'scope': 'Nested inclusive component timings, not additive; private executor evidence'}
        if not getattr(backend, '_performance_write_failed', False):
            try:
                with (backend.output / 'performance.jsonl').open('a') as stream:
                    stream.write(json.dumps(row) + '\n')
            except OSError as exc:
                # Optional timing must not mask a primary executor error or
                # abort a successful action before independent evaluation.
                backend._performance_write_failed = True
                failures = getattr(backend, 'diagnostic_write_failures', [])
                failures.append({'artifact': 'performance.jsonl', 'type': type(exc).__name__,
                                 'error': str(exc), 'at': row['at'], 'primitive': primitive})
                backend.diagnostic_write_failures = failures


def action_profile(method):
    @wraps(method)
    def wrapped(self, primitive, *args, **kwargs):
        with action_timing(self, primitive):
            return method(self, primitive, *args, **kwargs)
    return wrapped
