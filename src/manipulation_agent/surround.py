"""Asynchronous read-only four-camera jobs, serviced on the simulator owner thread."""
import copy
import time
from .contracts import SkillError
from .observations.boundary import public_execution_error
from .records import now, write_json

ACTIVE = {'planned', 'running'}


class SurroundJobs:
    def __init__(self, harness):
        self.h = harness
        self.jobs = {}
        self.active_id = None
        self.deadline = None

    @property
    def active(self):
        return self.active_id is not None

    def _save(self, job, event):
        folder = self.h.recorder.output / 'observation_jobs'
        folder.mkdir(exist_ok=True)
        write_json(folder / (job['job_id'] + '.json'), job)
        self.h.recorder.event(event, {'job': copy.deepcopy(job)})

    def start(self):
        if self.active:
            raise SkillError('observation_busy', 'An observation job is pending; get or cancel it.')
        ident = f'observation-{len(self.jobs)+1:04d}'
        job = {'job_id': ident, 'status': 'planned', 'created_at': now(), 'started_at': None,
               'finished_at': None, 'cancel_requested': False, 'capture': None,
               'error': None, 'submitted_revision': self.h.revision, 'observation': None}
        self.jobs[ident] = job
        self.active_id = ident
        self.deadline = min(self.h.started + self.h.budget.wall_seconds, time.monotonic() + 30)
        self._save(job, 'observation_planned')
        return self.get(ident)

    def get(self, job_id):
        if job_id not in self.jobs:
            raise SkillError('unknown_observation_job', 'Unknown observation job ID.')
        job = copy.deepcopy(self.jobs[job_id])
        observation = job.pop('observation')
        job['stale'] = bool(observation and observation != self.h.snapshot)
        job['poll_after_ms'] = 500 if job['status'] in ACTIVE else None
        result = {'job': job}
        if observation is not None:
            result['observation'] = observation
        return result

    def cancel(self, job_id):
        if job_id not in self.jobs:
            raise SkillError('unknown_observation_job', 'Unknown observation job ID.')
        job = self.jobs[job_id]
        if job['status'] in ACTIVE:
            job['cancel_requested'] = True
            self._save(job, 'observation_cancel_requested')
        return self.get(job_id)

    def _end(self, status, error=None):
        job = self.jobs[self.active_id]
        self.active_id = None
        job.update(status=status, finished_at=now(), error=error)
        self._save(job, 'observation_' + status)

    def stop_for_finish(self):
        if self.active:
            self._end('cancelled', {'code': 'episode_closed', 'message': 'Episode closed before capture.'})

    def tick(self):
        if not self.active:
            return
        job = self.jobs[self.active_id]
        if job['cancel_requested']:
            self._end('cancelled')
            return
        if time.monotonic() >= self.deadline:
            self._end('failed', {'code': 'observation_timeout', 'message': 'Observation job timed out.'})
            return
        if job['status'] == 'planned':
            job.update(status='running', started_at=now())
            self._save(job, 'observation_started')
            return  # allow independent RPCs between scheduling and the atomic render/capture
        try:
            observation = self.h.refresh()
            if {i['view'] for i in observation['images']} != {'front','back','left','right'}:
                raise RuntimeError('Expected four fixed camera views')
            job['observation'] = observation
            job['capture'] = observation['capture']
            self._end('passed')
        except Exception as exc:
            self.h.recorder.event('private_observation_error', {'job_id': job['job_id'],
                                                               'type': type(exc).__name__, 'detail': str(exc)})
            safe = exc if isinstance(exc, SkillError) else SkillError('execution_error', '')
            self._end('failed', public_execution_error(safe))
