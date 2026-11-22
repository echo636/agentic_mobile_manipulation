"""rc2 MAS_PLACE_STABLE_CHECK (2026-11-22, benchmark side, coordinator 10:59 II): a placement counts as placed only
when the item is still standing (if it was standing), its carried contents are still on it, and it has come to rest.

Split out of MAS_PLACE_TRANSACTIONAL (which from now on only decides whether a failed release is rolled back).
Why: TEA_TRACE_20261122 -- a tea bottle released 3 cm inside a tray edge fell over and lay still on the tray; every
existing check passed (it is on the tray, supported, at rest), so the receipt was `passed` 6/6. And `settled` never
counted (placement.py: residual motion was diagnostic only), so an item still rolling also passed.

When the switch is on, three post-release checks join the support check; the rc1 A5 settle fallback (same pose + up
to 2 nearby candidates) treats them like any other failed check:
  - upright_kept: an item that was standing when it was picked up (tilt <= 15 deg at its pre-grasp rest pose) must not
    end tilted > 45 deg; movable items standing on / in the target before the release must not end > 45 deg.
  - carried_contents_kept: when a carrier is put down, every carried item is still OnTop / Inside the carrier.
  - settled counts: after the normal settling, up to EXTRA_SETTLE_STEPS more simulation steps run while the measured
    pose window (RestingMotionWindow, 0.25 s, <= 2 mm, <= 2 deg) is not stable; still not stable = failed.
    Extra steps do not change robot time (the robot clock only notes that physics ran).
Independent of MAS_PLACE_TRANSACTIONAL: stable check alone -> a failed release ends as result_unconfirmed (the item
stays where it fell, the receipt says so); both on -> the release is rolled back and the receipt is not_placed.
Off (default): the 1d19707 behaviour.
"""
import math
import os

STANDING_MAX_DEG = 15.
FALLEN_MIN_DEG = 45.
EXTRA_SETTLE_STEPS = 100


def enabled():
    return os.environ.get('MAS_PLACE_STABLE_CHECK', 'off') == 'on'


def tilt_deg(q):
    x, y, z, w = (float(v) for v in q)
    upz = 1 - 2 * (x * x + y * y)
    return math.degrees(math.acos(max(-1., min(1., upz))))


def extra_settle_steps():
    return EXTRA_SETTLE_STEPS if enabled() else 0


def settled_counts():
    """Whether the `settled` check is part of the pass decision (it is diagnostic only when off)."""
    return enabled()


def _standing_neighbours(backend, held, target, payload):
    """Movable objects resting on / in the target before the release and standing (tilt <= 15 deg)."""
    from omnigibson.object_states import Inside, OnTop
    skip = {id(held), id(target), id(backend.robot), *[id(o) for o in payload]}
    out = []
    try:
        tlo, thi = [[float(v) for v in a] for a in target.aabb]
    except Exception:
        return out
    for obj in backend.env.scene.objects:
        if id(obj) in skip or getattr(obj, 'fixed_base', False):
            continue
        try:
            lo, hi = [[float(v) for v in a] for a in obj.aabb]
        except Exception:
            continue
        cx, cy = (lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2
        if not (tlo[0] <= cx <= thi[0] and tlo[1] <= cy <= thi[1] and lo[2] >= tlo[2] - .02 and lo[2] <= thi[2] + .05):
            continue
        try:
            on = (OnTop in obj.states and obj.states[OnTop].get_value(target)) or \
                 (Inside in obj.states and obj.states[Inside].get_value(target))
        except Exception:
            on = False
        if on and tilt_deg(obj.get_position_orientation()[1]) <= STANDING_MAX_DEG:
            out.append(obj)
    return out


def _clone_vec(v):
    """Copy a pose vector (torch tensor, ndarray, list or tuple) so physics steps cannot mutate it in place."""
    if hasattr(v, 'detach') and hasattr(v, 'clone'):
        return v.detach().clone()
    if hasattr(v, 'clone'):
        return v.clone()
    if hasattr(v, 'copy'):
        return v.copy()
    return tuple(v)


def before_release(backend, held, target, contents):
    """What must still hold after the release; read before the item leaves the hand. None when off."""
    if not enabled():
        return None
    payload = [obj for obj, _ in contents]
    ref = (getattr(backend, '_pregrasp_orientations', None) or {}).get(id(held))
    neighbours = _standing_neighbours(backend, held, target, payload)
    neighbour_poses = []
    for obj in neighbours:
        pos, ori = obj.get_position_orientation()
        neighbour_poses.append((obj, (_clone_vec(pos), _clone_vec(ori))))
    return {'held_standing': ref is not None and tilt_deg(ref) <= STANDING_MAX_DEG,
            'neighbours': neighbours,
            'neighbour_poses': neighbour_poses,
            'contents': payload}


def restore_neighbours(before):
    """Put standing neighbours back at their pre-release poses (with zero velocity) before an A5 retry."""
    if before is None:
        return
    for obj, (pos, ori) in before.get('neighbour_poses', ()):
        obj.set_position_orientation(_clone_vec(pos), _clone_vec(ori))
        if hasattr(obj, 'keep_still'):
            obj.keep_still()


def evaluate(held_tilt, neighbour_tilts, contents_on_carrier, before):
    """Pure check: dict of extra check -> bool (empty when the switch was off)."""
    if before is None:
        return {}
    checks = {'upright_kept': (not before['held_standing'] or held_tilt <= FALLEN_MIN_DEG)
                              and all(t <= FALLEN_MIN_DEG for t in neighbour_tilts)}
    if before['contents']:
        checks['carried_contents_kept'] = all(contents_on_carrier)
    return checks


def upright_failures(held, before):
    """Private diagnostic list of objects tilted > FALLEN_MIN_DEG; never enters model-visible receipts."""
    if before is None:
        return []
    out = []
    if before.get('held_standing') and held is not None:
        t = tilt_deg(held.get_position_orientation()[1])
        if t > FALLEN_MIN_DEG:
            out.append({'name': getattr(held, 'name', str(held)), 'role': 'held', 'tilt_deg': round(t, 2)})
    for obj in before.get('neighbours', ()):
        t = tilt_deg(obj.get_position_orientation()[1])
        if t > FALLEN_MIN_DEG:
            out.append({'name': getattr(obj, 'name', str(obj)), 'role': 'neighbour', 'tilt_deg': round(t, 2)})
    return out


def after_settle(backend, held, before):
    if before is None:
        return {}
    from omnigibson.object_states import Inside, OnTop

    def on_carrier(obj):
        try:
            return bool((OnTop in obj.states and obj.states[OnTop].get_value(held)) or
                        (Inside in obj.states and obj.states[Inside].get_value(held)))
        except Exception:
            return False
    return evaluate(tilt_deg(held.get_position_orientation()[1]),
                    [tilt_deg(o.get_position_orientation()[1]) for o in before['neighbours']],
                    [on_carrier(o) for o in before['contents']], before)


def passes(checks):
    """The pass decision over a place_on_top check dict: `settled` counts only when the switch is on."""
    return all(v for k, v in checks.items() if k != 'settled' or settled_counts())
