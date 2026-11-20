"""Measured resting-pose evidence, independent of noisy contact velocities."""
from collections import deque
import math


class RestingMotionWindow:
    """Require a full interval of small translation and orientation changes.

    Contacts can report residual solver velocities while the measured body
    stays still. No velocity or pose is modified to make this check pass.
    This is only settling evidence; the caller must separately prove support.
    """
    def __init__(self, dt, duration=.25, position_tolerance=.002,
                 orientation_tolerance=math.radians(2)):
        self.dt = dt
        self.size = math.ceil(duration/dt)+1
        self.samples = deque(maxlen=self.size)
        self.position_tolerance = position_tolerance
        self.orientation_tolerance = orientation_tolerance

    def add(self, position, orientation):
        if hasattr(position, 'tolist'):position = position.tolist()
        if hasattr(orientation, 'tolist'):orientation = orientation.tolist()
        p, q = tuple(map(float, position)), tuple(map(float, orientation))
        norm = math.sqrt(sum(v*v for v in q))
        if (len(p) != 3 or len(q) != 4 or not all(map(math.isfinite, (*p, *q)))
                or not math.isfinite(norm) or norm < 1e-12):
            self.samples.clear()
            return
        self.samples.append((p, tuple(v/norm for v in q)))

    def report(self):
        samples = list(self.samples)
        position_span = max((math.dist(a[0], b[0]) for a in samples for b in samples), default=0.)
        minimum_dot = min((abs(sum(x*y for x, y in zip(a[1], b[1])))
                           for a in samples for b in samples), default=1.)
        orientation_span = 2*math.acos(min(1., minimum_dot))
        complete = len(samples) == self.size
        return dict(method='measured_pose_window', sample_count=len(samples),
                    duration_seconds=max(0, len(samples)-1)*self.dt, complete=complete,
                    position_span_m=position_span, orientation_span_radians=orientation_span,
                    position_tolerance_m=self.position_tolerance,
                    orientation_tolerance_radians=self.orientation_tolerance,
                    stable=complete and position_span <= self.position_tolerance
                    and orientation_span <= self.orientation_tolerance)
