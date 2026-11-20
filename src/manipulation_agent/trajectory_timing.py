"""CPU-only whole-path timing and dense validation samples."""
import math
import numpy as np

MAX_CONTROL_STEPS = 100


def authored_path(positions, control_dt, *, control_steps=None):
    """Use the model's duration without joint-rate rejection or extension.

    Keep finite commands and both endpoints. The caller must collision-check
    the actual float32 commands and their interpolants before sending them.
    Omitted estimates retain the source duration; there is no one-step default.
    """
    source = np.asarray(positions, dtype=float)
    if (source.ndim != 2 or not all(source.shape) or not np.isfinite(source).all()
            or not math.isfinite(control_dt) or control_dt <= 0):
        raise ValueError('Invalid timed joint path')
    if control_steps is not None and (type(control_steps) is not int or control_steps < 1):
        raise ValueError('control_steps must be a positive integer estimate')
    steps = max(1, len(source)-1) if control_steps is None else control_steps
    t = np.linspace(0., 1., steps+1)
    at = (10*t**3 - 15*t**4 + 6*t**5)*(len(source)-1)
    q = np.stack([np.interp(at, np.arange(len(source)), c) for c in source.T], axis=1).astype(np.float32)
    q[0], q[-1] = source[0], source[-1]
    if not np.isfinite(q).all():
        raise ValueError('Joint commands exceed finite float32 representation')
    return q, dict(policy='model_authored_v1', source_control_steps=len(source)-1,
        requested_control_steps=control_steps, control_steps=steps, max_control_steps=None,
        sim_seconds=steps*control_dt, complete_path=True, estimate_extended=False,
        joint_limit_checks=False, time_parameterization='quintic_whole_path',
        duration_source='model' if control_steps is not None else 'planner',
        scope='model duration; collision and native tracking checked separately')


def compact_path(positions, control_dt, velocity, acceleration, jerk, *, active_indices=None,
                 max_steps=MAX_CONTROL_STEPS):
    """Preserve both endpoints, cap commands, check derivatives including rest.

    A failed timing check returns evidence, never an executable partial path.
    This is a timing feasibility test for the chosen curve, not a minimum-time
    optimizer or proof that native controllers can track it.
    """
    q = np.asarray(positions, dtype=float)
    if (q.ndim != 2 or len(q) < 1 or not np.isfinite(q).all() or
            not math.isfinite(control_dt) or control_dt <= 0 or type(max_steps) is not int or max_steps < 1):
        raise ValueError('Invalid timed joint path')
    indices = list(range(q.shape[1])) if active_indices is None else list(active_indices)
    rates = [np.asarray(value, dtype=float) for value in (velocity, acceleration, jerk)]
    if not indices or any(value.shape != (len(indices),) or not np.isfinite(value).all() or
                          np.any(value <= 0) for value in rates):
        raise ValueError('Missing finite positive limits for every active joint')
    original_steps = len(q)-1
    steps = min(max_steps, max(1, original_steps))
    if len(q) == 1:
        result = np.repeat(q, 2, axis=0)
    elif steps == original_steps:
        result = q.copy()
    else:
        source = np.arange(len(q))
        at = np.linspace(0, len(q)-1, steps+1)
        result = np.stack([np.interp(at, source, column) for column in q.T], axis=1)
    active = result[:, indices]
    # Include transitions from/to stationary commands at the call boundaries.
    padded = np.concatenate([np.repeat(active[:1], 3, axis=0), active,
                             np.repeat(active[-1:], 3, axis=0)])
    ratios = [float(np.max(np.abs(np.diff(padded, n=order, axis=0))/control_dt**order/rate))
              for order, rate in enumerate(rates, 1)]
    valid = max(ratios) <= 1. + 1e-6
    info = {'source_control_steps': original_steps, 'control_steps': steps,
            'max_control_steps': max_steps, 'sim_seconds': steps*control_dt,
            'complete_path': True, 'within_joint_limits': valid,
            'limit_ratios': dict(zip(('velocity', 'acceleration', 'jerk'), ratios)),
            'scope': 'configured joint-rate feasibility; native tracking is verified only after execution'}
    return (result if valid else None), info


def dense_joint_samples(positions, max_joint_increment=.02):
    """Check the command-to-command interpolants when timing removes samples."""
    q = np.asarray(positions, dtype=float)
    if q.ndim != 2 or len(q) < 2 or not np.isfinite(q).all() or max_joint_increment <= 0:
        raise ValueError('Invalid collision validation path')
    rows = [q[:1]]
    for a, b in zip(q, q[1:]):
        count = max(1, math.ceil(float(np.max(np.abs(b-a)))/max_joint_increment))
        rows.append(a + np.arange(1, count+1)[:, None]/count*(b-a))
    return np.concatenate(rows)


def estimated_path(positions, control_dt, velocity, acceleration, jerk, *,
                   active_indices=None, control_steps=None):
    """Model duration estimate -> rate-feasible complete float32 command path.

    Smooth time reparameterization keeps the source polyline and both endpoints.
    No fixed phase cap. Short estimates are extended before motion, never split
    into new task goals. Dense collision checks belong to the caller.
    """
    if control_steps is not None and (type(control_steps) is not int or control_steps < 1):
        raise ValueError('control_steps must be a positive integer estimate')
    source = np.asarray(positions, dtype=float)
    # Reuse strict shape, finite-value, timestep and rate validation.
    compact_path(source, control_dt, velocity, acceleration, jerk,
                 active_indices=active_indices, max_steps=1)
    steps = control_steps if control_steps is not None else max(1, len(source)-1)
    source_at = np.arange(len(source))
    for attempt in range(32):
        t = np.linspace(0., 1., steps+1)
        at = (10*t**3 - 15*t**4 + 6*t**5)*(len(source)-1)
        q = np.stack([np.interp(at, source_at, c) for c in source.T], axis=1).astype(np.float32)
        q[0], q[-1] = source[0], source[-1]
        checked, info = compact_path(q, control_dt, velocity, acceleration, jerk,
                                    active_indices=active_indices, max_steps=steps)
        info.update(source_control_steps=len(source)-1, requested_control_steps=control_steps,
                    max_control_steps=None, policy='model_estimated_v1',
                    time_parameterization='quintic_whole_path', timing_iterations=attempt+1,
                    estimate_extended=control_steps is not None and steps > control_steps)
        if checked is not None:
            return q, info
        ratios = list(info['limit_ratios'].values())
        factor = max(ratios[0], ratios[1]**.5, ratios[2]**(1/3))
        steps = max(steps+1, math.ceil(steps*max(1.1, factor*1.05)))
    # Explicit numerical failure, no executable partial path.
    info['failure'] = 'adaptive_timing_did_not_converge'
    return None, info
