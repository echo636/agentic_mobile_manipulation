"""rc2 MAS_PLACE_TRANSACTIONAL (2026-11-22, benchmark side, coordinator 05:43 / 05:46 / 06:04): a placement either
succeeds or the item is still in the hand.

When the switch is on:
1. Before a placement-type action the executor takes one whole-scene snapshot (og.sim.dump_state, the python RNGs,
   particle state, and its own carry / grasp bookkeeping) -- the same mechanism closure_clearance.py already uses for
   the closing preview. A 2026-11-22 probe (sec20/released_failures/RELEASED_FAILURES_20261122.md) restored the
   assisted grasp 6/6 (single item and loaded tray).
2. Two more post-release checks join the existing support check, so the rc1 A5 settle fallback (same pose + up to 2
   nearby candidates, <= 12 cm) treats them like any other failed check:
   - upright_kept: an item that was standing when it was picked up (tilt <= 15 deg at its pre-grasp rest pose) must
     not end tilted > 45 deg; items that were standing on / in the target before the release must not end > 45 deg.
   - carried_contents_kept: when a carrier is put down, every carried item is still OnTop / Inside the carrier.
3. When the release-time checks fail and A5 is used up, the scene is restored to the snapshot (item back in the hand)
   and the model receives the existing `not_placed` receipt ("The item was not placed. The item is still in your
   hand."); no new text. The release-time failure is still charged by the robot clock like any failed manipulation
   that stepped physics.
Off (default until the rc2 reference reruns): the 1d19707 behaviour -- the release is real even when its result
fails, receipt `result_unconfirmed`.
Private record: placement_diagnostics `status: transaction_rolled_back` / `transaction_not_rolled_back`.
"""
import copy
import math
import os
import random

STANDING_MAX_DEG = 15.
FALLEN_MIN_DEG = 45.
ROLLBACK_CODES = frozenset({'postcondition_error', 'physics_instability', 'target_changed'})
BOOKKEEPING = ('_grasped_payload', '_grasped_registered_ids', '_pregrasp_orientations', '_carrier_registry')


def enabled():
    return os.environ.get('MAS_PLACE_TRANSACTIONAL', 'off') == 'on'


def tilt_deg(q):
    x, y, z, w = (float(v) for v in q)
    upz = 1 - 2 * (x * x + y * y)
    return math.degrees(math.acos(max(-1., min(1., upz))))


def should_roll_back(exc):
    """Only failures after the item left the hand: the stages the model sees as result_unconfirmed / released."""
    from ..observations.boundary import RELEASED_STAGES
    return (getattr(exc, 'code', None) in ROLLBACK_CODES and bool(getattr(exc, 'changed', False))
            and getattr(exc, 'stage', None) in RELEASED_STAGES)


# ---------------------------------------------------------------- snapshot / restore (needs a simulator)

def snapshot(backend):
    import numpy as np
    from .particle_payload import snapshot_particle_state
    from .placement import scene_object_identities
    sim = backend.og.sim
    return {'state': sim.dump_state(serialized=False),
            'identities': scene_object_identities(sim),
            'particles': snapshot_particle_state(sim),
            'rng': (random.getstate(), np.random.get_state(), backend.torch.random.get_rng_state()),
            'book': {k: copy.copy(getattr(backend, k)) for k in BOOKKEEPING if hasattr(backend, k)},
            'held': backend._get_held()}


def restore(backend, snap):
    """Returns (ok, reason). Refuses when transition rules created or removed scene objects (slicing etc.)."""
    import numpy as np
    from .particle_payload import restore_particle_state
    from .placement import scene_object_identities
    sim = backend.og.sim
    if scene_object_identities(sim) != snap['identities']:
        return False, 'scene_objects_changed'
    sim.load_state(snap['state'], serialized=False)
    restore_particle_state(snap['particles'])
    random.setstate(snap['rng'][0]); np.random.set_state(snap['rng'][1]); backend.torch.random.set_rng_state(snap['rng'][2])
    for k, v in snap['book'].items():
        setattr(backend, k, v)
    backend.frames_revision = -1
    if backend._get_held() is not snap['held']:
        return False, 'grasp_not_restored'
    return True, None


# ---------------------------------------------------------------- post-release checks (pure logic is testable)

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


def before_release(backend, held, target, contents):
    """What must still hold after the release; read before the item leaves the hand."""
    if not enabled():
        return None
    payload = [obj for obj, _ in contents]
    ref = (getattr(backend, '_pregrasp_orientations', None) or {}).get(id(held))
    return {'held_standing': ref is not None and tilt_deg(ref) <= STANDING_MAX_DEG,
            'neighbours': _standing_neighbours(backend, held, target, payload),
            'contents': payload}


def evaluate(held_tilt, neighbour_tilts, contents_on_carrier, before):
    """Pure check: dict of extra check -> bool (empty when the switch was off)."""
    if before is None:
        return {}
    checks = {'upright_kept': (not before['held_standing'] or held_tilt <= FALLEN_MIN_DEG)
                              and all(t <= FALLEN_MIN_DEG for t in neighbour_tilts)}
    if before['contents']:
        checks['carried_contents_kept'] = all(contents_on_carrier)
    return checks


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
