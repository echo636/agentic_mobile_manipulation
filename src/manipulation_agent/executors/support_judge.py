"""rc2 MAS_SUPPORT_JUDGE (2026-11-22, benchmark side, coordinator 06:14 / 06:29):
a passed placement receipt <=> the task evaluator's native predicate also holds (two written exceptions).
Concretely: the item has settled, is released, is not held up by the robot, rests on an upward horizontal surface of
the target, AND native OnTop(target) or Inside(target) is true. Exceptions (receipt only, the evaluator is unchanged):
case (3) below (clicked a small item lying on a support; judged by the native OnTop on that support) and case (5)
(floor boundary). When the native predicate is false the placement fails after release: with MAS_PLACE_TRANSACTIONAL
on it is rolled back (not_placed), with it off it keeps the 1d19707 result_unconfirmed path.
The evaluator is NOT loosened (coordinator 06:29: e.g. the T1 coffee table's lower shelf must not count as on it). Independent of MAS_PLACE_TRANSACTIONAL; both off = the 1d19707 behaviour. Default off until the rc2
reference reruns.

Cases it changes (sec20/released_failures/JUDGE_MISMATCH_TABLE_20261122.md, table A):
 (1) place_on_top where native OnTop(target) is true but the executor's own patch checks failed because the item slid
     on the same target (selected_shelf_height / selected_surface_neighborhood): success.
 (2) place_inside into a target without a real cavity (movable and at most FLAT_MAX_HEIGHT_M tall: tray, plate, open
     carrier): judged like place_on_top -- native OnTop(target) true and settled = success.
 (3) place_on_top where the clicked target is a small movable non-carrier item lying on a support S (a half apple on
     a plate, a fork/bottle on a tray) and the item ends settled on S: success (coordinator choice (ii)). Carriers,
     plates, containers, and large horizontal supports (`min(dx, dy) >= 0.18 m`) are excluded so an item sliding off
     a tray/plate onto a bench/table underneath is never silently passed. Receipt only; the task evaluator still
     judges the relations written in the task goal.
(5) place_on_top onto a floor (coordinator 06:21): the support ray hits the target floor and the item touches only other
     floor pieces and fixed structure (walls, windows, doors, ceilings) -- e.g. a rag across two floor objects at a room
     boundary: the executor's contact-consistency check no longer fails it. Touching any other object still fails.
Not changed: an item hanging over a carrier edge (no upward support under it), a tray leaning on a plate (not
settled / no upward support).
Every rescue is recorded privately (placement_diagnostics `support_judge`), never shown to the model.
"""
import os

FLAT_MAX_HEIGHT_M = 0.08
UNDER_SUPPORT_MAX_MIN_HORIZ_M = 0.18
UNDER_SUPPORT_DISALLOWED_CATEGORIES = frozenset({
    'tray', 'plate', 'platter', 'dish', 'bowl', 'basket', 'box', 'carton', 'crate',
    'pan', 'pot', 'bucket', 'bin', 'cutting_board', 'chopping_board',
    'toolbox', 'suitcase', 'cooler', 'baking_sheet', 'cookie_sheet',
    'wicker_basket', 'packing_box', 'cardboard_box', 'storage_box', 'trash_can',
    'frying_pan', 'saucepan', 'stockpot', 'casserole', 'tupperware', 'colander',
    'laundry_basket', 'picnic_basket', 'shopping_basket', 'hamper',
})
NATIVE_EXCEPTIONS = frozenset({'on_support_under_clicked_item', 'floor_touching_only_structure'})
RESCUE_REQUIRED = ('released', 'upward_support', 'bottom_near_support', 'not_supported_by_robot')
STRUCTURE_CATEGORIES = frozenset({'floors', 'walls', 'ceilings', 'fixed_window', 'window', 'door', 'sliding_door',
                                  'garage_door', 'roof'})


def enabled():
    return os.environ.get('MAS_SUPPORT_JUDGE', 'off') == 'on'


def owner_name(prim_path):
    """'/World/scene_0/<object>/<link>/...' -> '<object>' (None when not under a scene)."""
    if not prim_path:
        return None
    parts = str(prim_path).split('/')
    return parts[3] if len(parts) > 3 and parts[2].startswith('scene_') else None


def is_flat_target(fixed_base, height_m):
    return (not fixed_base) and height_m is not None and height_m <= FLAT_MAX_HEIGHT_M


def judge_on_top(checks, official_on_top, support_owner, target_movable, target_support_name, extra_checks=None, *,
                 target_is_floor=False, contact_categories=None):
    """Pure decision for place_on_top after the normal checks failed. Returns (passed, reason)."""
    if not enabled():
        return False, None
    failed = {k for k, v in checks.items() if not v}
    if (target_is_floor and failed == {'selected_contact_consistent'} and contact_categories
            and all(c in STRUCTURE_CATEGORIES for c in contact_categories)
            and not (extra_checks and not all(extra_checks.values()))):
        return True, 'floor_touching_only_structure'           # case (5)
    if not all(checks.get(k) for k in RESCUE_REQUIRED) or not checks.get('settled', False):
        return False, None
    if extra_checks and not all(extra_checks.values()):
        return False, None                                     # MAS_PLACE_TRANSACTIONAL: fallen / contents lost
    if official_on_top:
        return True, 'native_on_top_same_target'               # case (1)
    if target_movable and target_support_name and support_owner == target_support_name:
        return True, 'on_support_under_clicked_item'           # case (3)
    return False, None


def judge_inside(official_inside, official_on_top, settled, flat_target, extra_checks=None):
    """Pure decision for place_inside. Returns (passed, reason)."""
    if official_inside or not enabled() or not flat_target or not settled:
        return False, None
    if extra_checks and not all(extra_checks.values()):
        return False, None
    return (True, 'flat_target_on_top') if official_on_top else (False, None)   # case (2)


def _is_container_target(target):
    """True when target is an actual container (fillable/openfillable cavity, or Contains/Open state).

    Note: OmniGibson puts `Inside` in `_DEFAULT_STATE_SET` on every object (including small non-containers like
    half_apple and fork), so `Inside in target.states` cannot distinguish containers from small items.
    """
    links = getattr(target, 'links', None)
    if isinstance(links, dict):
        for link in links.values():
            if (getattr(link, 'is_meta_link', False)
                    and getattr(link, 'meta_link_type', None) in {'fillable', 'openfillable'}):
                return True
    states = getattr(target, 'states', None)
    if states:
        try:
            import omnigibson.object_states as og_states
        except ImportError:
            og_states = None
        if og_states is not None:
            for attr in ('Contains', 'Open'):
                cls = getattr(og_states, attr, None)
                if cls is not None and cls in states:
                    return True
    return False


def allows_under_support_rescue(target):
    """Whether case (3) (`on_support_under_clicked_item`) may rescue a placement whose clicked target is `target`.

    True only for small movable non-carrier items lying on a support (e.g. a half apple on a plate, a fork/bottle on
    a tray); False for fixed furniture, carriers/plates/trays/containers, and large horizontal supports
    (`min(dx, dy) >= 0.18 m`), preventing an item sliding off a tray/plate onto a bench/table underneath from passing.
    """
    if target is None or getattr(target, 'fixed_base', False):
        return False
    cat = getattr(target, 'category', None)
    if cat is not None and str(cat).lower() in UNDER_SUPPORT_DISALLOWED_CATEGORIES:
        return False
    if _is_container_target(target):
        return False
    try:
        lo, hi = target.aabb
        dx = abs(float(hi[0]) - float(lo[0]))
        dy = abs(float(hi[1]) - float(lo[1]))
    except Exception:
        dx = dy = None
    if dx is not None and min(dx, dy) >= UNDER_SUPPORT_MAX_MIN_HORIZ_M:
        return False
    return True


# ------------------------------------------------------------------ simulator accessors

def support_under(backend, obj):
    """Name of the body directly under obj's bottom centre (downward ray, obj and robot ignored), else None."""
    if not enabled() or not allows_under_support_rescue(obj):
        return None
    try:
        from omnigibson.utils.sampling_utils import raytest
        lo, hi = obj.aabb
        start = backend.torch.tensor([float(lo[0] + hi[0]) / 2, float(lo[1] + hi[1]) / 2, float(lo[2]) + 0.01])
        end = start - backend.torch.tensor([0., 0., 0.10])
        ignored = [link.prim_path for o in (obj, backend.robot) for link in o.links.values()]
        hit = raytest(start, end, ignore_bodies=ignored)
        return owner_name(hit.get('rigidBody')) if hit.get('hit') else None
    except Exception:
        return None


def flat_target(target):
    try:
        lo, hi = target.aabb
        return is_flat_target(bool(getattr(target, 'fixed_base', False)), float(hi[2] - lo[2]))
    except Exception:
        return False


def contact_categories(backend, contact_paths):
    """Categories of the objects owning the contact bodies (None for an unknown body)."""
    out = []
    reg = getattr(backend.env.scene, 'object_registry', None)
    for path in contact_paths or ():
        name = owner_name(path)
        obj = reg('name', name) if (reg is not None and name) else None
        out.append(getattr(obj, 'category', None))
    return out


def native_gate(supported, official_on_top, official_inside, rescue_reason):
    """Last step for place_on_top: a passed result also needs the native OnTop / Inside of the target, except for the
    two written exceptions. Returns (passed, reason_if_blocked)."""
    if not enabled() or not supported:
        return supported, None
    if official_on_top or official_inside or rescue_reason in NATIVE_EXCEPTIONS:
        return True, None
    return False, 'native_predicate_false'
