"""Bounded, target-locked coordination; no simulator or model dependencies.

One brain action owns one controller. Internal motions do not consume MCP
turns, change public image revisions, or select another task/object.
"""
from dataclasses import asdict, dataclass
import math
import time
from ..contracts import SkillError


@dataclass(frozen=True)
class LocalLimits:
    radius_m: float = 2.0
    path_m: float = 4.0
    reach_m: float = 1.4
    preferred_reach_m: float = 1.25
    max_repositions: int = 3
    max_operation_attempts: int = 2
    max_env_steps: int = 600
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
        self.step_limit = min(max_steps, self.limits.max_env_steps)
        self.origin = tuple(backend._local_pose())
        self.last_pose = self.origin
        self.path = 0.
        self.repositions = self.attempts = 0
        self.visited = []
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
        if (self.clock()-self.started >= self.limits.wall_seconds or used >= self.step_limit
                or sampled >= self.limits.max_sampling_steps):
            raise SkillError('local_adjustment_exhausted', 'Local operation work limit reached', changed=self.changed)

    def admit_pose(self, pose):
        """Check every commanded pose BEFORE a base setter can move the robot."""
        self.check()
        distance = math.dist(self.last_pose[:2], pose[:2])
        if (math.dist(self.origin[:2], pose[:2]) > self.limits.radius_m+1e-6
                or self.path+distance > self.limits.path_m+1e-6):
            raise SkillError('local_adjustment_exhausted', 'Local motion boundary reached', changed=self.changed)
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
                if math.dist(pose[:2], point[:2]) > self.limits.reach_m+self.limits.radius_m:
                    raise SkillError('needs_macro_navigation', 'Target is outside local takeover range')
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
                            'protocol': 'target_locked_local_v1', 'repositions': self.repositions,
                            'primitive_attempts': self.attempts, 'path_m': self.path,
                            'env_steps': self.backend.steps-self.start_steps,
                            'sampling_steps': getattr(self.backend, 'sampling_physics_steps', 0)-self.start_sampling,
                            'wall_seconds': self.clock()-self.started, 'departure': departure}}
                if self.repositions >= self.limits.max_repositions:
                    raise SkillError('local_adjustment_exhausted', 'Local reposition limit reached')
                before = tuple(self.backend._local_pose())
                self.record('align')
                self.backend._local_reposition(self.primitive, obj, point, self)
                self.repositions += 1
                after = tuple(self.backend._local_pose())
                if (math.dist(before[:2], after[:2]) < .01 and
                        abs(math.atan2(math.sin(after[2]-before[2]), math.cos(after[2]-before[2]))) < math.radians(2)):
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
            self.record('failed', error_code=exc.code, detail=str(exc), world_may_have_changed=exc.changed)
            raise exc


def stance_candidates(actual, origin, point, limits, visited=()):
    """Prefer small adjustments; sample only the local disk, not the room."""
    candidates = []
    points = [tuple(actual[:2])]
    toward = math.atan2(point[1]-actual[1], point[0]-actual[0])
    # Sample the configured disk, retaining the denser small adjustments.
    distances = [.15, .3, .5, .75]
    distances.extend(i*.25 for i in range(4, math.ceil(limits.radius_m/.25)))
    distances.append(limits.radius_m)
    for distance in sorted({d for d in distances if d <= limits.radius_m}):
        for offset in (0, 45, -45, 90, -90, 135, -135, 180):
            yaw = toward+math.radians(offset)
            points.append((actual[0]+distance*math.cos(yaw), actual[1]+distance*math.sin(yaw)))
    for xy in points:
        if (math.dist(xy, origin[:2]) > limits.radius_m+1e-9 or
                math.dist(xy, point[:2]) > limits.preferred_reach_m):
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
        return math.dist(p[:2],actual[:2])/.5 + (angle(travel-actual[2])+angle(p[2]-travel))/(math.pi/3)
    return sorted(candidates,key=score)


class LocalCollision:
    """Restrict all search states and connectors to the same local disk."""
    def __init__(self,collision,origin,radius):
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
