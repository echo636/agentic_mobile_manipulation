"""Read native contact tensors only after Isaac physics warmup completes.

Restarting GPU physics invalidates old contact views during warmup. The native
post-step callback also runs for that warmup step, before the simulator refreshes
its views. Defer that one phase; retain native contact collection once ready.
"""
from pathlib import Path
import atexit
import json

PROTOCOL = 'native_contacts_after_physics_warmup_v1'


def install(record_path):
    from isaacsim.core.simulation_manager import SimulationManager
    from omnigibson.utils.usd_utils import RigidContactAPI

    original = RigidContactAPI.add_contacts_from_physics_step
    if getattr(original, '_manipulation_contact_warmup_guard', False):
        raise RuntimeError('Native contact warmup guard already installed')
    path = Path(record_path)
    counts = {'protocol': PROTOCOL, 'warmup_callbacks_deferred': 0,
              'native_contact_callbacks_completed': 0}
    def save():
        path.write_text(json.dumps(counts, indent=2) + '\n')
    save()
    def after_warmup():
        if SimulationManager._warmup_needed:
            counts['warmup_callbacks_deferred'] += 1
            save()
            return
        result = original()
        counts['native_contact_callbacks_completed'] += 1
        if counts['native_contact_callbacks_completed'] == 1:
            save()
        return result
    after_warmup._manipulation_contact_warmup_guard = True
    RigidContactAPI.add_contacts_from_physics_step = after_warmup
    atexit.register(save)
    return counts
