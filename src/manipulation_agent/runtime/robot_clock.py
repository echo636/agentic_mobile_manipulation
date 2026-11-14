"""Robot time: an accounting clock beside the simulator clock (benchmark side, always on).

Robot time = route time of every committed base motion (turns included) + MANIPULATION_SECONDS per charged
manipulation + the simulated seconds a wait actually waited. Nothing here advances physics and no executor
decision reads this clock. Ported from the frozen experiment variant source_infra_ops17 (robot_clock_v1) and
re-based on the native shortest-path executor.

Charging rules for one executor action (RGBBackend.execute_visual), identical for model episodes and
privileged references because both enter the executor there:
  travel        every base motion committed during the action, charged in _execute_base_path right after the
                pose transfer, so it counts even if the action then fails. A planned but uncommitted route costs
                nothing. Distance is the length of the committed route (the native shortest path, with the stubs
                to the exact start and endpoint). Time is distance / BASE_SPEED_M_S + turning / TURN_RATE.
  turning       the native route is a polyline on the map grid, so its raw heading changes are a grid artefact
                (measured on a 443 m kitchen reference: 1010 s raw). Turning is therefore measured on the route
                simplified to within TURN_SIMPLIFY_TOLERANCE_M (Douglas-Peucker; geometry only, no map), from
                the base yaw before the motion, along the simplified route, to the commanded final yaw.
  manipulation  MANIPULATION_SECONDS once per action whose primitive is not navigate_to, wait or look: when it
                succeeded, or when it failed after the manipulation itself started physically (the leaf executor
                stepped physics or placement-sampling physics, or reported a world change). release is charged
                when it succeeds or fails with a world change. Refusals before any attempt cost 0.
  wait          the executed simulated seconds (0 if the wait failed).
  look          its in-place turn, as a committed motion of zero length.
"""
import json
import math
import time

VERSION = 'robot_clock_v2_native_path'
BASE_SPEED_M_S = .5
TURN_RATE_RAD_S = math.pi / 3       # 60 degrees per second
MANIPULATION_SECONDS = 10.
TURN_SIMPLIFY_TOLERANCE_M = .10
NOT_MANIPULATION = frozenset({'navigate_to', 'wait', 'look'})
POLICY = {'version': VERSION, 'base_speed_m_s': BASE_SPEED_M_S, 'turn_rate_deg_s': 60.,
          'manipulation_seconds_each': MANIPULATION_SECONDS, 'wait': 'executed_simulated_seconds',
          'simulation_clock_advanced': False, 'travel_distance_measure': 'committed_native_route_length',
          'turn_measure': 'heading changes of the route simplified within the tolerance, from the yaw before '
                          'the motion to the commanded final yaw',
          'turn_simplify_tolerance_m': TURN_SIMPLIFY_TOLERANCE_M,
          'failed_manipulation': 'charged when the leaf executor stepped physics or reported a world change; '
                                 'refusals before an attempt are not charged',
          'committed_motion': 'charged at commit, also when the action later fails'}


def angle(value):
    return math.atan2(math.sin(value), math.cos(value))


def yaw_of_xyzw(quaternion):
    x, y, z, w = (float(v) for v in quaternion)
    return math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))


def _segment_distance(point, a, b):
    length = math.dist(a, b)
    if length < 1e-9:
        return math.dist(point, a)
    t = ((point[0] - a[0]) * (b[0] - a[0]) + (point[1] - a[1]) * (b[1] - a[1])) / (length * length)
    t = min(1., max(0., t))
    return math.dist(point, (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))


def simplify(points, tolerance=TURN_SIMPLIFY_TOLERANCE_M):
    """Douglas-Peucker: a subsequence of points, every dropped point within tolerance of the kept polyline."""
    points = [(float(p[0]), float(p[1])) for p in points]
    if len(points) < 3:
        return points
    keep = {0, len(points) - 1}
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        worst, index = -1., None
        for i in range(first + 1, last):
            distance = _segment_distance(points[i], points[first], points[last])
            if distance > worst:
                worst, index = distance, i
        if index is not None and worst > tolerance:
            keep.add(index)
            stack += [(first, index), (index, last)]
    return [points[i] for i in sorted(keep)]


def route_motion(points, start_yaw, end_yaw):
    """(distance_m, turn_radians, seconds) of one committed base motion.

    points is the committed route (one point for a turn in place). A yaw of None is skipped; offline use on
    old logs does not know the yaw before the first motion.
    """
    points = [(float(p[0]), float(p[1])) for p in points]
    distance = sum(math.dist(a, b) for a, b in zip(points, points[1:]))
    line = simplify(points)
    headings = [math.atan2(b[1] - a[1], b[0] - a[0]) for a, b in zip(line, line[1:]) if math.dist(a, b) > 1e-6]
    sequence = [yaw for yaw in (start_yaw, *headings, end_yaw) if yaw is not None]
    turn = sum(abs(angle(b - a)) for a, b in zip(sequence, sequence[1:]))
    if not all(math.isfinite(v) for v in (distance, turn)):
        raise ValueError(f'non-finite motion {(distance, turn)}')
    return distance, turn, distance / BASE_SPEED_M_S + turn / TURN_RATE_RAD_S


def clock_of(backend):
    """The episode's clock, created on first use. None if the backend cannot hold one."""
    clock = getattr(backend, 'robot_clock', None)
    if clock is None:
        try:
            clock = backend.robot_clock = RobotClock()
        except Exception:
            return None
    return clock


def charge_base_motion(backend, points, start_orientation, end_yaw):
    """Executor hook right after the base pose transfer. start_orientation is the base quaternion (xyzw)
    before the transfer. Returns the charged row or None; never raises."""
    clock = clock_of(backend)
    if clock is None:
        return None
    try:
        start_yaw = yaw_of_xyzw(start_orientation.tolist() if hasattr(start_orientation, 'tolist')
                                else start_orientation)
        distance, turn, seconds = route_motion(points, start_yaw, end_yaw)
        clock.add_motion(distance, seconds, turn_seconds=turn / TURN_RATE_RAD_S)
        return {'start_yaw': start_yaw, 'distance_m': distance, 'turn_radians': turn, 'seconds': seconds}
    except Exception as exc:  # accounting must never change execution
        clock.anomaly('motion', exc)
        return None


def clocked_leaf(backend, run):
    """Run the leaf manipulation executor unchanged; note whether it started physically."""
    clock = clock_of(backend)
    if clock is None:
        return run()
    try:
        mark = (backend.steps, getattr(backend, 'sampling_physics_steps', 0))
    except Exception as exc:
        clock.anomaly('leaf_begin', exc)
        return run()
    try:
        result = run()
    except BaseException as exc:
        clock.note_leaf(backend, mark, exc)
        raise
    clock.note_leaf(backend, mark, None)
    return result


def clocked_action(backend, primitive, options, run):
    """Run one executor action unchanged and settle its robot time. Nested actions are not settled twice."""
    clock = clock_of(backend)
    if clock is None or clock.in_action:
        return run()
    clock.begin_action(primitive)
    try:
        result = run()
    except BaseException as exc:
        clock.settle_action(primitive, error=exc)
        clock.end_action(backend, primitive, error=exc)
        raise
    clock.settle_action(primitive, details=result, requested_wait=(options or {}).get('seconds'))
    clock.end_action(backend, primitive)
    return result


def robot_clock_record(backend):
    """Totals for evaluation / run.json, or {} when the episode has no robot clock."""
    clock = getattr(backend, 'robot_clock', None)
    return clock.record() if isinstance(clock, RobotClock) else {}


class RobotClock:
    def __init__(self):
        self.travel_m = self.travel_s = self.turn_s = self.wait_s = 0.
        self.motions = self.manipulation_count = self.actions = 0
        self.failed_attempts_charged = self.refused_acts = 0
        self.by_kind = {}
        self.anomalies = []
        self.in_action = False
        self.last_action = None     # public block of the action that just ended
        self._kind = 'outside_action'
        self._attempts, self._motions, self._rule, self._mark = [], [], None, None

    @property
    def manipulation_s(self):
        return self.manipulation_count * MANIPULATION_SECONDS

    @property
    def total_s(self):
        return self.travel_s + self.manipulation_s + self.wait_s

    def anomaly(self, where, exc):
        if len(self.anomalies) < 50:
            self.anomalies.append(f'{where}: {type(exc).__name__}: {exc}')

    def add_motion(self, distance, seconds, *, turn_seconds=0.):
        if not all(math.isfinite(v) and v >= 0 for v in (distance, seconds, turn_seconds)):
            raise ValueError(f'non-finite or negative motion charge {(distance, seconds, turn_seconds)}')
        self.travel_m += distance
        self.travel_s += seconds
        self.turn_s += turn_seconds
        self.motions += 1
        row = self.by_kind.setdefault(self._kind, {'motions': 0, 'distance_m': 0., 'seconds': 0.})
        row['motions'] += 1
        row['distance_m'] += distance
        row['seconds'] += seconds
        self._motions.append({'kind': self._kind, 'distance_m': distance, 'seconds': seconds,
                              'turn_seconds': turn_seconds})

    def note_leaf(self, backend, mark, error):
        try:
            physics = (backend.steps > mark[0] or getattr(backend, 'sampling_physics_steps', 0) > mark[1])
            self._attempts.append({'ok': error is None, 'code': getattr(error, 'code', None),
                                   'changed': bool(getattr(error, 'changed', False)), 'physics': bool(physics)})
        except Exception as exc:
            self.anomaly('leaf_end', exc)

    def begin_action(self, primitive):
        self.in_action = True
        self._kind = ('look_turn' if primitive == 'look' else 'navigate_to' if primitive == 'navigate_to'
                      else 'act_internal')
        self._attempts, self._motions, self._rule = [], [], None
        self._mark = (self.travel_m, self.travel_s, self.manipulation_count, self.wait_s)

    def settle_action(self, primitive, *, details=None, error=None, requested_wait=None):
        """Charge manipulation or wait time for the action that just ended. Never raises."""
        try:
            if primitive == 'wait':
                if error is None:
                    seconds = details.get('sim_seconds') if isinstance(details, dict) else None
                    if not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds < 0:
                        seconds = 5. if requested_wait is None else float(requested_wait)
                    self.wait_s += float(seconds)
                    self._rule = 'wait_executed'
                else:
                    self._rule = 'wait_failed'
            elif primitive in NOT_MANIPULATION:
                self._rule = 'no_manipulation'
            elif error is None:
                self.manipulation_count += 1
                self._rule = 'manipulation_completed'
            elif (bool(getattr(error, 'changed', False)) if primitive == 'release' else
                  any(a['ok'] or a['changed'] or a['physics'] for a in self._attempts)):
                self.manipulation_count += 1
                self.failed_attempts_charged += 1
                self._rule = 'manipulation_attempt_failed'
            else:
                self.refused_acts += 1
                self._rule = 'refused_before_attempt'
        except Exception as exc:
            self.anomaly('settle', exc)

    def end_action(self, backend, primitive, *, error=None):
        """Close the action: keep its public block and append a private ledger row. Never raises."""
        try:
            mark = self._mark or (self.travel_m, self.travel_s, self.manipulation_count, self.wait_s)
            travel_m, travel_s = self.travel_m - mark[0], self.travel_s - mark[1]
            manipulation_s = (self.manipulation_count - mark[2]) * MANIPULATION_SECONDS
            wait_s = self.wait_s - mark[3]
            self.actions += 1
            self.last_action = {'this_call': {'travel_m': round(travel_m, 2), 'travel_s': round(travel_s, 1),
                                              'manipulation_s': round(manipulation_s, 1),
                                              'wait_s': round(wait_s, 1)},
                                'total_s': round(self.total_s, 1)}
            row = {'at_unix': time.time(), 'index': self.actions, 'primitive': primitive, 'ok': error is None,
                   'error': getattr(error, 'code', None) if error is not None else None,
                   'rule': self._rule or 'not_settled', 'motions': self._motions,
                   'leaf_attempts': self._attempts, 'travel_m': travel_m, 'travel_s': travel_s,
                   'manipulation_s': manipulation_s, 'wait_s': wait_s, 'total_s': self.total_s,
                   'audience': 'executor_private'}
            with (backend.output / 'robot_clock.jsonl').open('a') as stream:
                stream.write(json.dumps(row) + '\n')
        except Exception as exc:
            self.anomaly('end_action', exc)
        self.in_action = False
        self._kind = 'outside_action'
        self._attempts, self._motions, self._rule, self._mark = [], [], None, None

    def record(self):
        return {'robot_time_seconds': self.total_s, 'travel_distance_m': self.travel_m,
                'travel_seconds': self.travel_s, 'manipulation_count': self.manipulation_count,
                'manipulation_seconds': self.manipulation_s, 'wait_seconds': self.wait_s,
                'robot_clock': {**POLICY, 'committed_motions': self.motions, 'turn_seconds': self.turn_s,
                                'actions': self.actions,
                                'travel_by_kind': {k: dict(v) for k, v in self.by_kind.items()},
                                'failed_manipulation_attempts_charged': self.failed_attempts_charged,
                                'refused_manipulation_acts': self.refused_acts,
                                'ideal_navigation_distance_m_measure': 'sum of endpoint displacements',
                                'anomalies': list(self.anomalies)}}
