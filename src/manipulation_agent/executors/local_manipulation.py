"""Target-locked coordination with bounded work, not a start-centred travel disk.

One brain action owns one controller. Internal motions do not consume MCP
turns, change public image revisions, or select another task/object.
"""
from dataclasses import asdict, dataclass
import math
import time
from ..contracts import SkillError


@dataclass(frozen=True)
class LocalLimits:
    reach_m: float = 1.4
    preferred_reach_m: float = 1.25
    max_repositions: int = 3
    max_operation_attempts: int = 2
    max_env_steps: int = 600  # Atomic allowance; certified routes reserve extra steps.
    max_sampling_steps: int = 1200
    wall_seconds: float = 300.0
    planning_seconds: float = 12.0
    max_target_drift_m: float = .25

    def __post_init__(self):
        if any(not math.isfinite(v) or v <= 0 for v in asdict(self).values()):
            raise ValueError('Local operation limits must be positive and finite')
        if self.preferred_reach_m > self.reach_m:
            raise ValueError('Preferred reach must not exceed the executor limit')


class LocalOperation:
    def __init__(self, backend, primitive, binding, max_steps, options, *, clock=time.monotonic):
        self.backend, self.primitive, self.binding = backend, primitive, binding
        self.limits = backend.local_limits
        self.options = options
        self.clock, self.started = clock, clock()
        self.start_steps = backend.steps
        self.start_sampling = getattr(backend, 'sampling_physics_steps', 0)
        self.caller_step_limit = max_steps
        self.step_limit = min(max_steps, self.limits.max_env_steps)
        self.origin = tuple(backend._local_pose())
        self.last_pose = self.origin
        self.path = 0.
        self.repositions = self.attempts = 0
        self.visited = []
        self.changed = False
        self.route_started = None
        self.route_wall_seconds = 0.

    def record(self, phase, **data):
        self.backend._local_record(phase=phase, primitive=self.primitive,
                                   repositions=self.repositions, attempts=self.attempts,
                                   path_m=self.path, **data)

    def check(self, *, changed=False):
        self.backend.deadline.check(changed=changed or self.changed)
        used = self.backend.steps-self.start_steps
        sampled = getattr(self.backend, 'sampling_physics_steps', 0)-self.start_sampling
        self.changed |= changed or used > 0 or sampled > 0
        # Certified route execution has its own step bound and follower stall
        # checks. Rendering/I/O time must not consume the planning/manipulation
        # wall budget; external cancellation still applies on every step.
        active_route = 0. if self.route_started is None else self.clock()-self.route_started
        work_seconds = self.clock()-self.started-self.route_wall_seconds-active_route
        if (work_seconds >= self.limits.wall_seconds or used >= self.step_limit
                or sampled >= self.limits.max_sampling_steps):
            raise SkillError('local_adjustment_exhausted', 'Local operation work limit reached', changed=self.changed)

    def reserve_route(self, route, dt, *, settling_steps=10):
        """Budget a certified route before motion, retaining the caller's cap.

        Motion time follows the follower's rates. Two feedback steps per phase
        cover phase quantization/correction; ten settling steps are separate.
        The original atomic allowance remains available for manipulation.
        """
        self.check()
        if not math.isfinite(dt) or dt <= 0:
            raise ValueError('Positive finite control timestep required')
        seconds = route.estimated_motion_seconds
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError('Finite nonnegative certified motion time required')
        used = self.backend.steps-self.start_steps
        required = math.ceil(seconds/dt)+2*len(route.phases)+settling_steps
        remaining = self.caller_step_limit-used
        if required > remaining:
            self.record('route_budget_rejected', required_steps=required, remaining_steps=remaining)
            raise SkillError('local_adjustment_exhausted',
                             'Certified route exceeds the caller control-step budget before motion',
                             changed=self.changed, stage='local_control_budget')
        self.step_limit = min(self.caller_step_limit, self.step_limit+required)
        self.record('route_budget', required_steps=required, step_limit=self.step_limit,
                    caller_step_limit=self.caller_step_limit,
                    estimated_motion_seconds=seconds, phases=len(route.phases))
        self.route_started = self.clock()
        return min(required, self.step_limit-used)

    def finish_route(self):
        if self.route_started is not None:
            self.route_wall_seconds += self.clock()-self.route_started
            self.route_started = None

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
            while True:
                self.check()
                obj, point = self.backend._resolve_local_target(self.binding)
                self.backend._manipulation_preconditions(self.primitive, obj)
                pose = self.backend._local_pose()
                ready = self.backend._local_ready(self.primitive, obj, point, self)
                self.record('check', ready=ready)
                if ready:
                    if self.attempts >= self.limits.max_operation_attempts:
                        raise SkillError('local_adjustment_exhausted', 'Operation attempt limit reached')
                    self.attempts += 1
                    self.record('execute')
                    try:
                        result = self.backend._execute_bound_manipulation(
                            self.primitive, obj, point, self.binding.grounding,
                            self.step_limit-(self.backend.steps-self.start_steps), **self.options)
                    except SkillError as exc:
                        # A sampling/semantic/changed-world failure is NOT an
                        # invitation to blindly move or repeat the operation.
                        if exc.code != 'out_of_reach' or exc.changed:
                            raise
                        self.visited.append(tuple(pose))
                        self.record('recover', reason=exc.code)
                    else:
                        # The primitive already checked its postcondition.
                        # Never repeat a committed effect if departure fails.
                        self.changed = True
                        departure = self.backend._local_departure(self, obj)
                        self.record('complete', departure=departure)
                        return {**result, 'local_manipulation': {
                            'protocol': 'target_locked_approach_v2', 'repositions': self.repositions,
                            'primitive_attempts': self.attempts, 'path_m': self.path,
                            'env_steps': self.backend.steps-self.start_steps,
                            'sampling_steps': getattr(self.backend, 'sampling_physics_steps', 0)-self.start_sampling,
                            'wall_seconds': self.clock()-self.started, 'departure': departure}}
                if self.repositions >= self.limits.max_repositions:
                    raise SkillError('local_adjustment_exhausted', 'Local reposition limit reached')
                before = tuple(self.backend._local_pose())
                carry_before = getattr(self, 'carry_posture_revision', 0)
                self.record('align')
                self.backend._local_reposition(self.primitive, obj, point, self)
                self.repositions += 1
                after = tuple(self.backend._local_pose())
                if (math.dist(before[:2], after[:2]) < .01 and
                        abs(math.atan2(math.sin(after[2]-before[2]), math.cos(after[2]-before[2]))) < math.radians(2)
                        and getattr(self, 'carry_posture_revision', 0) == carry_before):
                    raise SkillError('local_adjustment_blocked', 'Local adjustment made no progress')
                self.visited.append(before)
                # Refresh only internal sensor packets; never re-ground an old
                # pixel in a new image. The bound link-relative point is used.
                self.backend._local_reobserve(self.binding)
                self.record('reobserve')
        except SkillError as exc:
            if exc.code == 'out_of_reach':
                exc = SkillError('local_adjustment_blocked',str(exc),changed=exc.changed)
            exc.changed |= self.changed or self.backend.steps > self.start_steps
            self.record('failed', error_code=exc.code, detail=str(exc), world_may_have_changed=exc.changed,
                        failure_stage=exc.stage)
            raise exc


def stance_candidates(actual, origin, point, limits, visited=()):
    """Propose poses around the selected target, ranked from the actual pose."""
    candidates = []
    points = [tuple(actual[:2])]
    toward = math.atan2(point[1]-actual[1], point[0]-actual[0])
    # Target-centred rings include the side facing the robot and alternatives
    # around the object. The full reachable disk is covered by the grid fallback.
    distances = {.5, .75, 1., limits.preferred_reach_m, limits.reach_m}
    for distance in sorted(d for d in distances if d <= limits.reach_m):
        # The base follower accepts a 2mm position residual. Keep selected
        # endpoints 5mm inside reach so a valid arrival is also operation-ready.
        distance = min(distance, limits.reach_m-.005)
        for i in range(16):
            yaw = toward+math.pi+i*math.pi/8
            points.append((float(point[0])+distance*math.cos(yaw),
                           float(point[1])+distance*math.sin(yaw)))
    for xy in points:
        if math.dist(xy, point[:2]) > limits.reach_m+1e-9:
            continue
        bearing = math.atan2(point[1]-xy[1], point[0]-xy[0])
        travel = math.atan2(xy[1]-actual[1], xy[0]-actual[0]) if math.dist(xy,actual[:2])>.01 else actual[2]
        for yaw in (actual[2], travel, bearing, bearing+math.pi/2, bearing-math.pi/2, bearing+math.pi):
            pose = (*xy, math.atan2(math.sin(yaw), math.cos(yaw)))
            if any(math.dist(pose[:2], p[:2]) < .05 and
                   abs(math.atan2(math.sin(yaw-p[2]),math.cos(yaw-p[2]))) < math.radians(10) for p in (*visited,*candidates)):
                continue
            candidates.append(pose)
    def score(p):
        angle = lambda a: abs(math.atan2(math.sin(a), math.cos(a)))
        travel = math.atan2(p[1]-actual[1],p[0]-actual[0]) if math.dist(p[:2],actual[:2])>.01 else actual[2]
        preference = max(0., math.dist(p[:2],point[:2])-limits.preferred_reach_m)
        return math.dist(p[:2],actual[:2])/.5 + (angle(travel-actual[2])+angle(p[2]-travel))/(math.pi/3) + preference
    # Interleave approach sides instead of exhausting a single blocked patch.
    # Distance from the starting pose affects ranking only, never admission.
    buckets = {}
    for pose in sorted(candidates,key=score):
        dx,dy=pose[0]-actual[0],pose[1]-actual[1]
        distance=math.hypot(dx,dy)
        side=round(math.atan2(math.sin(math.atan2(dy,dx)-toward),
                              math.cos(math.atan2(dy,dx)-toward))/(math.pi/2)) if distance>.01 else 0
        band=0 if distance<=.5+1e-8 else 1 if distance<=1.+1e-8 else 2
        buckets.setdefault((band,side),[]).append(pose)
    ordered=[]
    while any(buckets.values()):
        for group in buckets.values():
            if group:ordered.append(group.pop(0))
    return ordered


class LocalCollision:
    """Restrict all search states and connectors to the same local disk."""
    def __init__(self,collision,origin,radius):
        if hasattr(collision,'local_window'):collision=collision.local_window(origin,radius)
        self.collision,self.origin,self.radius=collision,origin,radius
        self.grid=self._restrict(collision.grid)

    def _restrict(self,grid):
        from .gt_navigation import GridMap
        free=bytearray(grid.width*grid.height)
        row,col=grid.cell(self.origin[:2]);cells=math.ceil(self.radius/grid.resolution)+1
        for r in range(max(0,row-cells),min(grid.height,row+cells+1)):
            for c in range(max(0,col-cells),min(grid.width,col+cells+1)):
                if math.dist(grid.world((r,c)),self.origin[:2])<=self.radius:
                    free[r*grid.width+c]=grid.free[r*grid.width+c]
        return GridMap(grid.width,grid.height,grid.resolution,grid.origin,bytes(free))

    def motion_free(self,a,b):
        return (all(math.dist(p[:2],self.origin[:2])<=self.radius+1e-9 for p in (a,b))
                and self.collision.motion_free(a,b))

    def optimistic_grid(self):return self._restrict(self.collision.optimistic_grid())

    def guidance_grid(self):
        method = getattr(self.collision,'guidance_grid',self.collision.optimistic_grid)
        return self._restrict(method())
