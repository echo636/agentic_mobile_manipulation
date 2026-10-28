"""Episode shutdown progress, separate from task scores and client liveness."""
from pathlib import Path
import threading
import time

from ..records import write_json


class EpisodeClosure:
    """Only lifecycle metadata is shared with HTTP threads; never simulator state.

    A sent HTTP reply is not proof that the model received it. Model receipt is
    checked independently against the controller transcript.
    """
    def __init__(self, output):
        self.path = Path(output) / 'closure.json'
        self.lock = threading.Lock()
        self.state = {'phase': 'running'}

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def advance(self, phase, **fields):
        with self.lock:
            if self.state['phase'] == phase:
                return
            at = time.time()
            self.state = {**self.state, **fields, 'phase': phase,
                          'phase_started_at_unix': at, phase + '_at_unix': at}
            write_json(self.path, self.state)

    def reply_sent(self):
        with self.lock:
            if self.state.get('phase') != 'result_ready' or self.state.get('reply_sent_at_unix'):
                return
            self.state = {**self.state, 'reply_sent_at_unix': time.time()}
            write_json(self.path, self.state)


def closure_watchdog(row, *, now, stage_seconds=600, client_seconds=120):
    """Return a bounded cleanup reason without timing the robot's execution.

    Each meaningful closure phase gets its own deadline. Repeated polls do not
    renew it, and persisted timestamps survive supervisor adoption. A legacy
    closed flag has unknown progress and receives the scoring-stage allowance.
    """
    state = row.get('episode_closure') or {}
    phase = state.get('phase', 'legacy_closed')
    if phase == 'running':
        return None
    monitor = row.get('closure_watchdog') or {}
    if monitor.get('phase') != phase:
        monitor = {'phase': phase, 'started_at_unix': now}
        row['closure_watchdog'] = monitor
    budget = client_seconds if phase in {'result_ready', 'simulator_exited'} else stage_seconds
    if now - monitor['started_at_unix'] < budget:
        return None
    return 'client_exit_timeout' if phase == 'result_ready' else phase + '_timeout'
