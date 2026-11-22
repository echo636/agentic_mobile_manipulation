"""rc2 record layer (2026-11-22, coordinator 14:00 II; KNOWN_ISSUES #23): executor-private per-frame occupancy and per-capture
posture, so self / held-object / camera-in-geometry occlusion can be counted exactly in later episodes. Records only --
nothing here changes behaviour or anything the model receives (the private executor_frames/*.json and
observation_capture_audit.jsonl are never sent to the model)."""
import numpy as np

NEAR_DEPTH_M = 0.15


def posture(robot):
    """Head / torso / neck joint angles (radians) by joint name."""
    try:
        names = list(robot.joints.keys())
        jp = robot.get_joint_positions()
        return {n: round(float(jp[i]), 4) for i, n in enumerate(names) if ('torso' in n or 'head' in n or 'neck' in n)}
    except Exception:
        return None


def occupancy(depth, ids=None, labels=None, robot_prefix=None, held_prefixes=()):
    """Fractions of the image: near_geometry (depth < 0.15 m), and -- with renderer pixel ownership -- robot (pixels whose
    owning prim path starts with the robot prim path) and held (pixels of the held object / its carried items)."""
    d = np.asarray(depth, dtype=float)
    if d.ndim == 3:
        d = d[..., 0]
    ok = np.isfinite(d) & (d > 0)
    out = {'near_geometry': round(float((ok & (d < NEAR_DEPTH_M)).mean()), 4)}
    if ids is None or labels is None:
        return out
    ids = np.asarray(ids)
    if ids.ndim == 3:
        ids = ids[..., 0]
    robot_ids = [int(k) for k, v in labels.items() if robot_prefix and str(v).startswith(robot_prefix)]
    held_ids = [int(k) for k, v in labels.items() if any(str(v).startswith(p) for p in held_prefixes if p)]
    out['robot'] = round(float(np.isin(ids, robot_ids).mean()), 4) if robot_ids else 0.0
    out['held'] = round(float(np.isin(ids, held_ids).mean()), 4) if held_ids else 0.0
    return out
