"""One absolute episode deadline, shared across startup, policy and actions.

Checks are cooperative. A supervisor owns the hard process limit when a native
call cannot return to Python; score availability never determines a timeout.
"""
from dataclasses import dataclass
import math
import os
import time

from .contracts import SkillError


@dataclass(frozen=True)
class EpisodeDeadline:
    unix: float | None = None

    def __post_init__(self):
        if self.unix is not None and (not math.isfinite(self.unix) or self.unix <= 0):
            raise ValueError('Episode deadline must be a positive finite Unix timestamp')

    @classmethod
    def from_env(cls, *, default_unix=None):
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
