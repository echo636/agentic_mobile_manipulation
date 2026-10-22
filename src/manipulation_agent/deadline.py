"""Private execution clock shared by controller and simulator after startup.

Checks are cooperative. A supervisor owns the hard process limit when a native
call cannot return to Python; score availability never determines a timeout.
"""
import fcntl
import json
import math
import os
from pathlib import Path
import tempfile
import threading
import time
import sys

from .contracts import SkillError

FINISH_GRACE_SECONDS = 120
UNBOUNDED_TOOL_TIMEOUT_SECONDS = 2**31 - 1
# OmniGibson and executor APIs require an integer step limit. Managed episodes
# use their wall-clock deadline; this sentinel disables a second episode cutoff.
DEADLINE_STEP_SENTINEL = sys.maxsize


def tool_wait_seconds(execution_seconds):
    """Transport waits may cover execution plus closure, never shorten the task."""
    # Codex requires a numeric MCP timeout. This is a transport sentinel, not
    # an episode deadline; step-budget controllers use communicate(timeout=None).
    if execution_seconds is None:return UNBOUNDED_TOOL_TIMEOUT_SECONDS
    return math.ceil(float(execution_seconds) + FINISH_GRACE_SECONDS)


def validate_execution_clock(value):
    """Validate clock data without accepting a new start or extending its budget."""
    if not isinstance(value,dict):raise ValueError('Execution clock must be a JSON object')
    if value.get('budget_policy') in {'agent_steps', 'unbounded'}:
        started=value.get('execution_started_at_unix');steps=value.get('max_agent_steps')
        if (isinstance(started,bool) or not isinstance(started,(int,float)) or
                not math.isfinite(started) or started<=0):
            raise ValueError('Execution clock requires a finite positive start')
        if value['budget_policy']=='agent_steps' and (type(steps) is not int or steps<=0):
            raise ValueError('Step clock requires a positive step limit')
        if value['budget_policy']=='unbounded' and steps is not None:
            raise ValueError('Unbounded episodes must not carry an action limit')
        if value.get('episode_deadline_unix') is not None or value.get('execution_budget_seconds') is not None:
            raise ValueError('Step-only episodes must not carry a wall-clock deadline')
        return dict(value)
    keys=('execution_started_at_unix','episode_deadline_unix','execution_budget_seconds')
    for key in keys:
        number=value.get(key)
        if isinstance(number,bool) or not isinstance(number,(int,float)) or not math.isfinite(number) or number<=0:
            raise ValueError('Execution clock requires positive finite '+key)
    if abs(value['episode_deadline_unix']-value['execution_started_at_unix']-value['execution_budget_seconds'])>.001:
        raise ValueError('Execution clock deadline does not match its start and duration')
    return dict(value)


def write_execution_clock(path, duration_seconds, *, max_agent_steps=None, unbounded=False):
    """Atomically arm once, called privately after MCP handshake and before policy.

    Repeated calls return the existing clock, including when already expired.
    The model has neither this command nor access to its file.
    """
    step_only=max_agent_steps is not None
    if unbounded and (duration_seconds is not None or step_only):
        raise ValueError('Unbounded clock cannot include action or duration limits')
    if step_only and (duration_seconds is not None or type(max_agent_steps) is not int or max_agent_steps<=0):
        raise ValueError('Step-only clock requires positive max_agent_steps and no duration')
    if not (step_only or unbounded) and (isinstance(duration_seconds,bool) or not isinstance(duration_seconds,(int,float)) or not math.isfinite(duration_seconds) or duration_seconds<=0):
        raise ValueError('Execution duration must be positive and finite')
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.with_suffix(path.suffix+'.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if path.exists():return validate_execution_clock(json.loads(path.read_text()))
        started=time.time()
        record=validate_execution_clock({'schema_version':1,'clock_kind':'policy_execution',
            **({'budget_policy':'unbounded','max_agent_steps':None} if unbounded else
               {'budget_policy':'agent_steps','max_agent_steps':max_agent_steps} if step_only else {}),
            'execution_started_at_unix':started,'episode_deadline_unix':None if step_only or unbounded else started+duration_seconds,
            'execution_budget_seconds':duration_seconds,'writer_pid':os.getpid()})
        temporary=None
        try:
            with tempfile.NamedTemporaryFile(mode='w',dir=path.parent,prefix=path.name+'.',delete=False) as stream:
                temporary=Path(stream.name)
                json.dump(record,stream,allow_nan=False);stream.write('\n');stream.flush();os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            if temporary is not None:temporary.unlink(missing_ok=True)
        return record


class EpisodeDeadline:
    def __init__(self, unix=None, *, clock_path=None):
        if unix is not None and (isinstance(unix,bool) or not math.isfinite(unix) or unix<=0):
            raise ValueError('Episode deadline must be a positive finite Unix timestamp')
        self._unix=unix
        self.clock_path=Path(clock_path) if clock_path is not None else None
        self._clock=None
        self._stop_requested=threading.Event()

    def request_stop(self):
        """Thread-safe intent only; physics and scoring still run on the owner."""
        self._stop_requested.set()

    @property
    def stop_requested(self):
        return self._stop_requested.is_set()

    @property
    def managed(self):
        """A pending external clock must suppress legacy ready-time timers too."""
        return self.clock_path is not None or self._unix is not None or self._clock is not None

    def _load_clock(self):
        if self.clock_path is None or self._clock is not None:return
        try:raw=self.clock_path.read_text()
        except FileNotFoundError:return
        self._clock=validate_execution_clock(json.loads(raw))
        self._unix=self._clock['episode_deadline_unix']

    @property
    def unix(self):
        self._load_clock()
        return self._unix

    @property
    def clock(self):
        self._load_clock()
        return dict(self._clock) if self._clock is not None else None

    def arm_local(self, duration_seconds, *, max_agent_steps=None, unbounded=False):
        """Standalone fallback at bridge readiness, never during construction."""
        if not self.managed:
            started=time.time()
            self._clock=validate_execution_clock({'clock_kind':'standalone_ready',
                **({'budget_policy':'unbounded','max_agent_steps':max_agent_steps} if unbounded else
                   {'budget_policy':'agent_steps','max_agent_steps':max_agent_steps} if max_agent_steps is not None else {}),
                'execution_started_at_unix':started,'episode_deadline_unix':None if duration_seconds is None else started+duration_seconds,
                'execution_budget_seconds':duration_seconds})
            self._unix=self._clock['episode_deadline_unix']
        return self.clock

    @classmethod
    def from_env(cls, *, default_unix=None):
        clock_path=os.environ.get('MAS_EXECUTION_CLOCK_PATH')
        if clock_path:return cls(clock_path=clock_path)
        value = os.environ.get('MAS_EPISODE_DEADLINE_UNIX')
        return cls(float(value) if value is not None else default_unix)

    @property
    def expired(self):
        return self.unix is not None and time.time() >= self.unix

    def remaining(self, limit=None):
        remaining = None if self.unix is None else max(0.0, self.unix - time.time())
        return limit if remaining is None else remaining if limit is None else min(limit, remaining)

    def check(self, *, changed=False):
        if self.expired:
            raise SkillError('episode_timeout', 'The episode wall-clock deadline has expired.', changed=changed)
        if self.stop_requested:
            raise SkillError('episode_cancelled', 'Episode closure requested; stop the active action before scoring.', changed=changed)
