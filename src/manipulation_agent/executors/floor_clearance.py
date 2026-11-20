"""rc1 A3 (2026-11-20, benchmark side): the base is never set down on, or against, a small loose object lying on the floor.

Base motion is a symbolic transfer to the route endpoint (no driving is simulated) and endpoints are chosen on OmniGibson's
static traversability map, which does not contain small movable objects. A base placed on top of such an object pushes it
away (v12b terra T4: book 0.12 m, camera 0.27 m, neither ever grasped). Every standing cell chosen by `_navigate`
(object approach, floor click, and the local reposition before a grasp/place, which goes through `_navigate`) therefore
keeps clearance from floor items:
- floor item: a non-fixed object whose AABB bottom is within FLOOR_ITEM_MAX_Z of the floor height and whose larger
  horizontal AABB side is at most FLOOR_ITEM_MAX_XY; the robot and the held object are excluded. Computed at every
  navigation from executor state; never shown to the model.
- clearance: the item's horizontal AABB grown by base radius + CLEARANCE_MARGIN_M; when no candidate is left, by the
  base radius alone; when still none, the v12b selection (no clearance). Every relaxation is recorded privately.
- target that is itself a floor item: stand at least TARGET_MIN_DISTANCE_M from it so it is not under the robot.
Switch: MAS_FLOOR_CLEARANCE=off restores v12b behaviour (default on).
"""
import os

import numpy as np

FLOOR_ITEM_MAX_Z = 0.10
FLOOR_ITEM_MAX_XY = 0.60
CLEARANCE_MARGIN_M = 0.10
TARGET_MIN_DISTANCE_M = 0.60
EXCLUDED_CATEGORIES = ('floors', 'walls', 'ceilings', 'carpet', 'rug', 'agent', 'door')


def enabled():
    return os.environ.get('MAS_FLOOR_CLEARANCE', 'on') != 'off'


def base_radius(robot):
    """Half the horizontal diagonal of the robot's reset chassis AABB (OmniGibson erodes by this + 0.2 m)."""
    extent = np.asarray([float(v) for v in robot.reset_joint_pos_aabb_extent[:2]])
    return float(np.linalg.norm(extent) / 2.0)


def is_floor_item(lo, hi, floor_z, fixed, category):
    if fixed or any(c in (category or '') for c in EXCLUDED_CATEGORIES):
        return False
    if float(lo[2]) - float(floor_z) > FLOOR_ITEM_MAX_Z:
        return False
    return max(float(hi[0]) - float(lo[0]), float(hi[1]) - float(lo[1])) <= FLOOR_ITEM_MAX_XY


def floor_items(objects, floor_z, exclude=()):
    """[(name, (xmin, ymin), (xmax, ymax))] of loose floor items among `objects` (OmniGibson objects)."""
    out = []
    for obj in objects:
        name = getattr(obj, 'name', None)
        if name is None or name in exclude:
            continue
        try:
            lo, hi = obj.aabb
            fixed = bool(getattr(obj, 'fixed_base', False))
            category = str(getattr(obj, 'category', ''))
        except Exception:
            continue
        if is_floor_item(lo, hi, floor_z, fixed, category):
            out.append((name, (float(lo[0]), float(lo[1])), (float(hi[0]), float(hi[1]))))
    return out


def blocked(points_xy, items, grow):
    """Boolean per point: inside some item's horizontal AABB grown by `grow` metres."""
    pts = np.asarray(points_xy, dtype=float).reshape(-1, 2)
    mask = np.zeros(len(pts), dtype=bool)
    for _, lo, hi in items:
        mask |= ((pts[:, 0] >= lo[0] - grow) & (pts[:, 0] <= hi[0] + grow)
                 & (pts[:, 1] >= lo[1] - grow) & (pts[:, 1] <= hi[1] + grow))
    return mask


def tiers(radius):
    """Clearance tiers tried in order; None = v12b selection without clearance."""
    return [('radius_plus_margin', radius + CLEARANCE_MARGIN_M), ('radius', radius), ('v12b_no_clearance', None)]


def candidate_mask(points_xy, items, grow, target_xy=None, target_is_floor_item=False):
    """Cells allowed under one tier: outside every grown item; for a floor-item target also >= TARGET_MIN_DISTANCE_M."""
    pts = np.asarray(points_xy, dtype=float).reshape(-1, 2)
    if grow is None:
        return np.ones(len(pts), dtype=bool)
    ok = ~blocked(pts, items, grow)
    if target_is_floor_item and target_xy is not None:
        ok &= np.linalg.norm(pts - np.asarray(target_xy, dtype=float)[None, :2], axis=1) >= TARGET_MIN_DISTANCE_M
    return ok
