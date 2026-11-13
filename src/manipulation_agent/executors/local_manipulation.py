"""Target-locked coordination with bounded work, not a start-centred travel disk.

One brain action owns one controller. Internal motions do not consume MCP
turns, change public image revisions, or select another task/object.
"""
from dataclasses import asdict, dataclass
import math
import time
from ..contracts import SkillError
from ..runtime.robot_clock import clocked_leaf


@dataclass(frozen=True)
class LocalLimits:
    reach_m: float = 1.4
    # Used only when the static map leaves no standing cell within reach_m.
    fallback_reach_m: float = 2.0
    # A floor click on a cell the base cannot occupy moves to walkable floor this close to it.
    floor_click_snap_m: float = 2.0
    max_env_steps: int = 600  # Atomic manipulation allowance, separate from episode policy.
    max_sampling_steps: int = 1200
    wall_seconds: float = 300.0

    def __post_init__(self):
        if any(not math.isfinite(v) or v <= 0 for v in asdict(self).values()):
            raise ValueError('Local operation limits must be positive and finite')


class LocalOperation:
    def __init__(self, backend, primitive, binding, max_steps, options, *, clock=time.monotonic):
        self.backend, self.primitive, self.binding = backend, primitive, binding
        self.limits = backend.local_limits
        self.reach_m = self.limits.reach_m  # widened by the approach only for a reach fallback
        self.options = options
        self.clock, self.started = clock, clock()
        self.start_steps = backend.steps
        self.start_sampling = getattr(backend, 'sampling_physics_steps', 0)
        self.step_limit = min(max_steps, self.limits.max_env_steps)
        self.origin = tuple(backend._local_pose())
        self.last_pose = self.origin
        self.path = 0.
        self.repositions = self.attempts = 0
        self.changed = False

    def record(self, phase, **data):
        self.backend._local_record(phase=phase, primitive=self.primitive,
                                   repositions=self.repositions, attempts=self.attempts,
                                   path_m=self.path, **data)

    def check(self, *, changed=False):
        self.backend.deadline.check(changed=changed or self.changed)
        used = self.backend.steps-self.start_steps
        sampled = getattr(self.backend, 'sampling_physics_steps', 0)-self.start_sampling
        self.changed |= changed or used > 0 or sampled > 0
        work_seconds = self.clock()-self.started
        if (work_seconds >= self.limits.wall_seconds or used >= self.step_limit
                or sampled >= self.limits.max_sampling_steps):
            raise SkillError('local_adjustment_exhausted', 'Local operation work limit reached',
                             changed=self.changed, stage='local_work_budget')

    def admit_pose(self, pose):
        """Check every commanded pose BEFORE a base setter can move the robot."""
        self.check()
        distance = math.dist(self.last_pose[:2], pose[:2])
        self.path += distance
        self.last_pose = tuple(pose)
        self.changed = True

    def run(self):
        self.record('bind', binding=self.binding.describe(), limits=asdict(self.limits))
        try:
            self.check()
            obj,point=self.backend._resolve_local_target(self.binding)
            if not self.backend._local_ready(self.primitive,obj,point,self):
                # Reject an impossible operation before moving. The leaf
                # checks current hand/object state after the physical step.
                self.backend._manipulation_preconditions(self.primitive,obj)
                self.record('align')
                self.backend._local_reposition(self.primitive,obj,point,self)
                self.repositions=1
                obj,point=self.backend._resolve_local_target(self.binding)
                if not self.backend._local_ready(self.primitive,obj,point,self):
                    raise SkillError('out_of_reach','Selected surface is still beyond reach after the approach')
            self.check()
            self.attempts=1
            self.record('execute')
            # Robot clock (accounting only): note whether the manipulation started physically.
            result=clocked_leaf(self.backend,lambda:self.backend._execute_bound_manipulation(
                self.primitive,obj,point,self.binding.grounding,
                self.step_limit-(self.backend.steps-self.start_steps),**self.options))
            self.changed=True
            self.record('complete')
            return {**result,'local_manipulation':{
                'protocol':'target_locked_single_approach_v3','repositions':self.repositions,
                'primitive_attempts':self.attempts,'path_m':self.path,
                'env_steps':self.backend.steps-self.start_steps,
                'sampling_steps':getattr(self.backend,'sampling_physics_steps',0)-self.start_sampling,
                'wall_seconds':self.clock()-self.started}}
        except SkillError as exc:
            if exc.code=='out_of_reach':
                exc=SkillError('local_adjustment_blocked',str(exc),changed=exc.changed)
            exc.changed |= self.changed or self.backend.steps>self.start_steps
            self.record('failed',error_code=exc.code,detail=str(exc),world_may_have_changed=exc.changed,
                        failure_stage=exc.stage)
            raise exc
