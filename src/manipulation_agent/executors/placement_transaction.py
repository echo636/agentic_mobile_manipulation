"""rc2 MAS_PLACE_TRANSACTIONAL (2026-11-22, benchmark side, coordinator 05:43 / 05:46 / 06:04; narrowed 10:59): a
placement either succeeds or the item is still in the hand. This switch only decides what happens to a FAILED release.

When the switch is on:
1. Before a placement-type action the executor takes one whole-scene snapshot (og.sim.dump_state, the python RNGs,
   particle state, and its own carry / grasp bookkeeping) -- the same mechanism closure_clearance.py already uses for
   the closing preview. A 2026-11-22 probe (sec20/released_failures/RELEASED_FAILURES_20261122.md) restored the
   assisted grasp 6/6 (single item and loaded tray).
2. When the release-time checks fail and A5 is used up, the scene is restored to the snapshot (item back in the hand)
   and the model receives the existing `not_placed` receipt ("The item was not placed. The item is still in your
   hand."); no new text. The release-time failure is still charged by the robot clock like any failed manipulation
   that stepped physics.
Which checks a release must pass is not decided here: upright_kept / carried_contents_kept / settled moved to
MAS_PLACE_STABLE_CHECK (placement_stable.py, coordinator 10:59 II); the two switches are independent.
Off (default until the rc2 reference reruns): the 1d19707 behaviour -- the release is real even when its result
fails, receipt `result_unconfirmed`.
Private record: placement_diagnostics `status: transaction_rolled_back` / `transaction_not_rolled_back`.
"""
import copy
import os
import random

from .placement_stable import tilt_deg  # noqa: F401  (kept here: probes import it from this module)

ROLLBACK_CODES = frozenset({'postcondition_error', 'physics_instability', 'target_changed'})
BOOKKEEPING = ('_grasped_payload', '_grasped_registered_ids', '_pregrasp_orientations', '_carrier_registry')


def enabled():
    return os.environ.get('MAS_PLACE_TRANSACTIONAL', 'off') == 'on'


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
