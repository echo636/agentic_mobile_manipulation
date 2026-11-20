"""rc1 A6 (2026-11-20, benchmark side, defect #24): carrier contents registry.

Before (v12b): contents of a carrier (tray, basket, plate, ...) were decided once, when the carrier was grasped, from
the physical state at that moment (official Inside, or OnTop + Touching, or a "rider" whose every contact is the
carrier), and dropped if they touched anything else. While held they stayed on the carrier by friction only. Items the
robot had just placed on a tray could be left behind (two touching items each have a non-member contact) and the
placement receipt still reported success.

After (switch MAS_CARRIER_REGISTRY, default OFF since 2026-11-20 21:00 -- tray probe on/off showed no difference and the
motivating defect #24 was voided by the recount; on = registry, off/unset = v12b behaviour):
1. A successful place_on_top / place_inside onto a MOVABLE object (not fixed_base) registers held -> target. No task
   role is read; any movable support counts. A registration that would make a cycle is refused.
2. Grasping a carrier carries every registered descendant (layer by layer) without the external-contact filter, unless
   it has clearly left the carrier (official OnTop/Inside both false AND outside the carrier's footprint (+2 cm) /
   0-10 cm band above its top), which unregisters it. Unregistered things (e.g. already on the carrier in the initial
   scene) keep the v12b physical rule, including the external-contact filter.
3. While the carrier is held, registered contents are put back at their grasp-time pose relative to the carrier after
   every physics step when they drifted (> 5 mm or > 3 deg); particles they hold move with them (same snapshot /
   relocate as a directly held container at a symbolic transfer). Not friction.
4. Registrations stay when the carrier is put down; grasping a registered item on its own unregisters it.
5. Capacity is not a new rule: an item that does not fit gets no placement pose. Receipts are unchanged.
Every register / unregister / refusal / drift correction writes a private carry_diagnostics.jsonl record.
"""
import math
import os

FOOTPRINT_MARGIN_M = 0.02
ABOVE_TOP_M = 0.10
BELOW_TOP_M = 0.02
PIN_POSITION_TOL_M = 0.005
PIN_ANGLE_TOL_RAD = math.radians(3.0)


def enabled():
    return os.environ.get('MAS_CARRIER_REGISTRY', 'off') == 'on'


class Registry:
    """child -> parent edges keyed by object identity. Pure bookkeeping (unit-tested without a simulator)."""

    def __init__(self):
        self.parent_of = {}

    def register(self, child, parent):
        if child is parent:
            return 'refused_self'
        if any(node is parent for node, _ in self.descendants(child)):
            return 'refused_cycle'
        previous = self.parent_of.get(id(child))
        self.parent_of[id(child)] = (child, parent)
        return 'moved' if previous is not None and previous[1] is not parent else 'registered'

    def unregister(self, child):
        return self.parent_of.pop(id(child), None) is not None

    def parent(self, child):
        entry = self.parent_of.get(id(child))
        return None if entry is None else entry[1]

    def children(self, parent):
        return [c for c, p in self.parent_of.values() if p is parent]

    def descendants(self, root, live_ids=None):
        """[(child, parent)] breadth-first; each node once (cycle-safe)."""
        out, seen, queue = [], {id(root)}, [root]
        while queue:
            node = queue.pop(0)
            for child in self.children(node):
                if id(child) in seen or (live_ids is not None and id(child) not in live_ids):
                    continue
                seen.add(id(child)); out.append((child, node)); queue.append(child)
        return out

    def prune(self, live_ids):
        dead = [k for k, (c, p) in self.parent_of.items() if id(c) not in live_ids or id(p) not in live_ids]
        for k in dead:
            del self.parent_of[k]
        return len(dead)


def still_on(child_lo, child_hi, parent_lo, parent_hi, official):
    """True when the child has not clearly left the carrier: official OnTop/Inside, or its centre is over the
    carrier footprint (+margin) with its bottom in the band [top - 2 cm, top + 10 cm] (or inside the carrier box)."""
    if official:
        return True
    cx = (child_lo[0] + child_hi[0]) / 2; cy = (child_lo[1] + child_hi[1]) / 2
    m = FOOTPRINT_MARGIN_M
    over = parent_lo[0] - m <= cx <= parent_hi[0] + m and parent_lo[1] - m <= cy <= parent_hi[1] + m
    bottom = child_lo[2]
    return over and parent_lo[2] - BELOW_TOP_M <= bottom <= parent_hi[2] + ABOVE_TOP_M


def registry(backend):
    reg = getattr(backend, '_carrier_registry', None)
    if reg is None:
        reg = Registry()
        backend._carrier_registry = reg
    return reg


def _record(backend, **data):
    if hasattr(backend, '_carry_record') and hasattr(backend, 'output'):
        backend._carry_record(component='rc1_A6_carrier_registry', **data)


def _live_ids(backend):
    scene = getattr(getattr(backend, 'env', None), 'scene', None)
    return None if scene is None else {id(o) for o in scene.objects}


def register_after_place(backend, held, target, primitive):
    """Called after a placement receipt passed (no exception)."""
    if not enabled() or held is None or target is None or getattr(target, 'fixed_base', True):
        return None
    result = registry(backend).register(held, target)
    _record(backend, event='carrier_register', result=result, primitive=primitive,
            child=getattr(held, 'name', None), carrier=getattr(target, 'name', None))
    return result


def unregister_on_grasp(backend, obj):
    """Grasping a registered item on its own takes it off its carrier."""
    if not enabled():
        return False
    reg = registry(backend)
    parent = reg.parent(obj)
    if parent is None:
        return False
    reg.unregister(obj)
    _record(backend, event='carrier_unregister', reason='grasped_on_its_own',
            child=getattr(obj, 'name', None), carrier=getattr(parent, 'name', None))
    return True


def _official_on(child, parent):
    states = getattr(child, 'states', {})
    try:
        from omnigibson.object_states import Inside, OnTop
    except Exception:  # unit tests
        return False
    for state in (OnTop, Inside):
        if state in states:
            try:
                if states[state].get_value(parent):
                    return True
            except Exception:
                pass
    return False


def grasp_contents(backend, carrier):
    """Registered descendants still on their carrier, as dependency triples (child, parent, 'Registered').
    Those that clearly left are unregistered here (private record)."""
    if not enabled():
        return []
    reg = registry(backend)
    live = _live_ids(backend)
    if live is not None:
        reg.prune(live)
    kept, kept_ids = [], {id(carrier)}
    for child, parent in reg.descendants(carrier, live):
        if id(parent) not in kept_ids:
            continue            # its own carrier left, so it is not part of this assembly
        clo, chi = [[float(v) for v in a] for a in child.aabb]
        plo, phi = [[float(v) for v in a] for a in parent.aabb]
        if still_on(clo, chi, plo, phi, _official_on(child, parent)):
            kept.append((child, parent, 'Registered')); kept_ids.add(id(child))
        else:
            reg.unregister(child)
            _record(backend, event='carrier_unregister', reason='left_carrier', child=child.name,
                    carrier=parent.name, child_aabb=[clo, chi], carrier_aabb=[plo, phi])
    if kept:
        _record(backend, event='carrier_grasp_contents', carrier=getattr(carrier, 'name', None),
                registered=[[c.name, p.name] for c, p, _ in kept])
    return kept


def _angle_between(q1, q2):
    dot = abs(sum(float(a) * float(b) for a, b in zip(q1, q2)))
    return 2 * math.acos(min(1.0, dot))


def pin_contents(backend):
    """After a physics step while a carrier is held: put drifted registered contents back (with their particles)."""
    if not enabled():
        return 0
    saved = getattr(backend, '_grasped_payload', None)
    ids = getattr(backend, '_grasped_registered_ids', None)
    if not saved or not ids:
        return 0
    held = saved[0]
    if backend._get_held() is not held:
        return 0
    from omnigibson.utils import transform_utils as T
    from .particle_payload import snapshot_particles, relocate_particles
    pose = held.get_position_orientation()
    fixed = []
    for child, relative in saved[2]:
        if id(child) not in ids:
            continue
        want_p, want_q = T.pose_transform(*pose, *relative)
        p, q = child.get_position_orientation()
        drift = float(sum((float(a) - float(b)) ** 2 for a, b in zip(p, want_p)) ** .5)
        if drift <= PIN_POSITION_TOL_M and _angle_between(q, want_q) <= PIN_ANGLE_TOL_RAD:
            continue
        particles = snapshot_particles(backend.env.scene, [child], include_supported=True)
        child.set_position_orientation(want_p, want_q)
        if hasattr(child, 'keep_still'):
            child.keep_still()
        relocate_particles(backend.env.scene, particles)
        fixed.append([child.name, round(drift, 4)])
    if fixed:
        _record(backend, event='carrier_pin', carrier=held.name, corrected=fixed)
    return len(fixed)
