"""CPU geometry for parallel-jaw grasps. No simulator or learned grasp policy.

Contact frames are derived from measured mesh sections, then transformed to EEF
poses, following RoboTwin's contact-frame -> gripper-frame convention. This first
implementation deliberately supports elongated, approximately upright objects.
"""
from dataclasses import dataclass
import numpy as np


@dataclass
class GraspCandidate:
    contact: np.ndarray
    rotation: np.ndarray
    eef: np.ndarray
    pregrasp: np.ndarray
    width: float
    score: float


def bottle_grasps(vertices, hand_position, contact_offset, aperture, clearance=.10, faces=None):
    points = np.asarray(vertices, dtype=float)
    if points.ndim != 2 or points.shape[1] != 3 or len(points) < 8 or not np.isfinite(points).all():
        raise ValueError('Need finite collision mesh vertices')
    center = (points.min(0) + points.max(0)) / 2
    _, axes = np.linalg.eigh(np.cov((points-center).T))
    up = axes[:, -1]
    up *= 1 if up[2] >= 0 else -1
    if up[2] < .90:
        raise ValueError('Initial geometric pick supports approximately upright elongated objects')
    heights = (points-center) @ up
    lo, hi = heights.min(), heights.max()
    if hi-lo < .08:
        raise ValueError('Object too short for the bottle geometry model')
    toward = np.asarray(hand_position)-center
    toward -= toward.dot(up)*up
    if np.linalg.norm(toward) < 1e-6:
        toward = np.cross(up, [0, 1, 0])
    toward /= np.linalg.norm(toward)
    tangent = np.cross(up, toward)
    result = []
    edges = None
    if faces is not None:
        triangles = np.asarray(faces, dtype=int).reshape(-1, 3)
        edges = np.unique(np.sort(np.concatenate([triangles[:, [0, 1]],
            triangles[:, [1, 2]], triangles[:, [2, 0]]]), axis=1), axis=0)
    # Prefer the body, but include the neck when body diameter exceeds aperture.
    for fraction in (.50, .65, .78, .87, .35):
        height = lo + fraction*(hi-lo)
        if edges is None:
            section = points[np.abs(heights-height) < max(.012, .08*(hi-lo))]
        else:
            a, b = edges[:, 0], edges[:, 1]
            dz = heights[b]-heights[a]
            valid = (np.abs(dz) > 1e-8) & ((heights[a]-height)*(heights[b]-height) <= 0)
            a, b, dz = a[valid], b[valid], dz[valid]
            section = points[a]+((height-heights[a])/dz)[:, None]*(points[b]-points[a])
        if len(section) < 4:
            continue
        c = (section.min(0)+section.max(0))/2
        c += up*(height-(c-center).dot(up))
        for yaw in (0, -.35, .35, -.70, .70, -1.05, 1.05):
            outward = np.cos(yaw)*toward + np.sin(yaw)*tangent
            # EEF +z along fingers, +y along jaw closing, per OG convention.
            for tilt in (0, .30):
                z = -outward*np.cos(tilt)-up*np.sin(tilt)
                y = np.cross(up, z); y /= np.linalg.norm(y)
                x = np.cross(y, z)
                rotation = np.column_stack((x, y, z))
                width = float(np.ptp(section @ y))
                if not .006 < width < aperture-.006:
                    continue
                eef = c-rotation@np.asarray(contact_offset)
                score = float(np.linalg.norm(eef-hand_position)+abs(yaw)*.08+abs(fraction-.5)*.15)
                result.append(GraspCandidate(c, rotation, eef, eef-z*clearance, width, score))
    return sorted(result, key=lambda c: c.score)


def resample_positions(position, source_dt, control_dt, slowdown=1.5):
    """Consume planner timing once; stream one command each control tick.

    Slowing a trajectory scales its velocity and acceleration down. It is not a
    substitute for collision checking: the final resampled path is checked again.
    """
    q = np.asarray(position, dtype=float)
    if q.ndim != 2 or len(q) < 2 or not np.isfinite(q).all():
        raise ValueError('Invalid timed trajectory')
    if min(source_dt, control_dt) <= 0 or slowdown < 1:
        raise ValueError('Invalid trajectory timing')
    duration = (len(q)-1)*source_dt*slowdown
    times = np.arange(int(np.ceil(duration/control_dt))+1)*control_dt
    source_times = np.arange(len(q))*source_dt*slowdown
    return np.column_stack([np.interp(times, source_times, q[:, i]) for i in range(q.shape[1])])


def time_joint_curve(waypoints, velocity, acceleration, jerk, control_dt):
    """Time a geometric IK curve with rest endpoints and measured joint limits."""
    from scipy.interpolate import CubicSpline
    points = np.asarray(waypoints, dtype=float)
    limits = [np.asarray(value, dtype=float) for value in (velocity, acceleration, jerk)]
    if (points.ndim != 2 or len(points) < 2 or not np.isfinite(points).all()
            or control_dt <= 0 or any(value.shape != (points.shape[1],) or
                not np.isfinite(value).all() or np.any(value <= 0) for value in limits)):
        raise ValueError('Invalid geometric path or timing limits')
    curve = CubicSpline(np.linspace(0, 1, len(points)), points, axis=0)
    duration = max(1., float(np.max(np.sum(np.abs(np.diff(points, axis=0)), axis=0)/limits[0]))*2)
    for _ in range(12):
        count = max(4, int(np.ceil(duration/control_dt)))
        t = np.linspace(0, 1, count+1)
        q = curve(10*t**3-15*t**4+6*t**5)
        ratios = [float(np.max(np.abs(np.diff(q, n=order, axis=0))/control_dt**order/limit))
                  for order, limit in enumerate(limits, 1)]
        factor = max(value**(1/order) for order, value in enumerate(ratios, 1))
        if factor <= 1:
            return q, dict(duration_s=count*control_dt, limit_ratios=ratios)
        duration = count*control_dt*factor*1.05
    raise ValueError('Could not time geometric path within joint limits')


def separating_contact_path(depths, carried_mask, positions, rotations, up):
    """Certify departure from existing shallow support contact, never a new one.

    Depths are per-sphere full-world penetration from the unchanged checker.
    Only initially contacting carried spheres may remain in contact while the
    EEF rises without lateral motion or rotation. Every other pair stays clear.
    A separate mesh certificate must identify the initial support and its normal.
    """
    d, mask, p, r, up = (np.asarray(value) for value in
                        (depths, carried_mask, positions, rotations, up))
    if (d.ndim != 2 or len(d) < 2 or mask.shape != (d.shape[1],) or
            p.shape != (len(d), 3) or r.shape != (len(d), 3, 3) or up.shape != (3,)
            or any(not np.isfinite(value).all() for value in (d, p, r, up))
            or not np.isclose(np.linalg.norm(up), 1)):
        raise ValueError('Invalid separation certificate inputs')
    initial = (d[0] > 0) & mask.astype(bool)
    contacting = np.flatnonzero(np.any(d > 0, axis=1))
    last = int(contacting[-1])+1 if len(contacting) else 0
    delta = p-p[0]
    heights = delta @ up
    lateral = np.linalg.norm(delta-heights[:, None]*up, axis=1)
    relative = r @ r[0].T
    angles = np.arccos(np.clip((np.trace(relative, axis1=1, axis2=2)-1)/2, -1, 1))
    # Global USD root coordinates are ~300m: float32 FK resolution is ~30um.
    epsilon = 5e-5
    checks = dict(
        shallow_initial_contact=bool(d[0].max() <= .002),
        no_new_collision=bool(np.all(d[:, ~initial] <= 0)),
        decreasing_existing_contact=bool(np.all(d[:, initial] <= d[0, initial]+epsilon) and
            np.all(np.diff(d[:, initial], axis=0) <= epsilon)),
        fully_separated=bool(np.all(d[-1] <= 0) and last < len(d)),
        rises_from_support=bool(np.all(np.diff(heights[:last+1]) >= -epsilon)),
        no_contact_sliding=bool(lateral[:last+1].max() <= .001),
        no_contact_rotation=bool(angles[:last+1].max() <= .003),
        separates_within_3mm=bool(np.all(d[heights >= .003] <= 0)))
    return dict(valid=all(checks.values()), checks=checks,
                initial_depth_m=float(d[0].max()), last_contact_sample=last-1,
                max_contact_lateral_m=float(lateral[:last+1].max()))


def body_path_envelope(positions, rotations, initial_height, restore=False):
    """Check every body sample, including low or tilted intermediate poses."""
    p, r = np.asarray(positions), np.asarray(rotations)
    if (p.ndim != 2 or p.shape[1] != 3 or len(p) == 0 or r.shape != (len(p), 3, 3)
            or not np.isfinite(p).all() or not np.isfinite(r).all() or not np.isfinite(initial_height)):
        raise ValueError('Invalid measured body path')
    tilts = np.degrees(np.arccos(np.clip(r[:, 2, 2], -1, 1)))
    valid = bool(tilts.max() <= 12 and p[:, 2].min() >= initial_height-.45)
    if restore:
        valid &= bool(tilts[-1] <= 4 and p[-1, 2] >= initial_height-.06)
    return {'valid': valid, 'max_tilt_deg': float(tilts.max()),
            'minimum_height_world_m': float(p[:, 2].min()), 'restores_initial_body': restore}


def attachment_pose_in_base(eef_position, eef_quaternion, base_position, base_quaternion):
    """Express measured EEF pose in the frame used by OG's obstacle meshes."""
    from scipy.spatial.transform import Rotation
    ep, eq, bp, bq = [np.asarray(value, dtype=float) for value in
                      (eef_position, eef_quaternion, base_position, base_quaternion)]
    if (ep.shape != (3,) or bp.shape != (3,) or eq.shape != (4,) or bq.shape != (4,)
            or not all(np.isfinite(value).all() for value in (ep, eq, bp, bq))):
        raise ValueError('Attachment requires finite measured poses')
    inverse = Rotation.from_quat(bq).inv()
    return inverse.apply(ep-bp), (inverse * Rotation.from_quat(eq)).as_quat()


def recorded_pick_reference(events, request_id, target_id):
    """Recover measurements before the pick that established current retention.

    No model pose or new recovery posture is accepted. Missing or occupied-hand
    provenance fails before planning, rather than redefining the crouch as home.
    """
    observation = reference_event = None
    for event in events:
        if (event.get('kind') == 'tool_call' and event.get('name') == 'manip'
                and event.get('request_id') == request_id):
            break
        candidate = event.get('observation') or event.get('result', {}).get('observation')
        if isinstance(candidate, dict) and candidate.get('oracle_3d'):
            observation, reference_event = candidate, event.get('id')
    else:
        raise ValueError('Retention source pick request is missing')
    if observation is None:
        raise ValueError('Missing measured pre-pick observation')
    geometry = observation['oracle_3d']
    robot = geometry['robot']
    if any(hand.get('held_object') is not None for hand in robot['arms'].values()):
        raise ValueError('Retention source must begin with empty hands')
    targets = [obj for obj in geometry['objects'] if obj['id'] == target_id]
    if len(targets) != 1:
        raise ValueError('Missing or ambiguous reference target')
    from scipy.spatial.transform import Rotation
    base = robot['base_pose_world']
    target_world = (np.asarray(base['position_m']) + Rotation.from_quat(
        base['quaternion_xyzw']).apply(targets[0]['pose']['position_m']))
    body = robot['trunk']['body_posture']['upper_body_pose_world']
    values = [*target_world, *body['position_m'], *body['quaternion_xyzw']]
    if not np.isfinite(values).all():
        raise ValueError('Non-finite pick reference')
    return dict(body_position_world_m=body['position_m'],
                body_quaternion_xyzw=body['quaternion_xyzw'],
                target_position_world_m=target_world.tolist(),
                reference_revision=observation['revision'], reference_event_id=reference_event,
                source_request_id=request_id, target_object_id=target_id)
