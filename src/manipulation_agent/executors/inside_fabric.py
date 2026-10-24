"""Keep native containment sampling geometry current with its physics trials."""
from functools import wraps


def install_inside_sampling_fabric_sync():
    import omnigibson as og
    from omnigibson.object_states import Inside

    original = Inside._set_value
    if not getattr(original, '_mas_inside_fabric_sync', False):
        @wraps(original)
        def sample(state, *args, **kwargs):
            sim = og.sim
            step = sim.step_physics

            def synchronized_step(*step_args, **step_kwargs):
                result = step(*step_args, **step_kwargs)
                # The pinned step_physics updates PhysX but leaves Fabric at
                # the last commanded pose. Native Inside reads its AABB from
                # Fabric, so an ejected object can otherwise still look inside.
                sim.sync_physx_to_fabric()
                return result

            sim.step_physics = synchronized_step
            try:
                sim.sync_physx_to_fabric()
                return original(state, *args, **kwargs)
            finally:
                sim.step_physics = step

        sample._mas_inside_fabric_sync = True
        Inside._set_value = sample
    return {'status': 'configured', 'scope': 'native_Inside_sampling_only',
            'physics_steps_contacts_predicate_and_geometry': 'unchanged',
            'change': 'Synchronize physical poses to Fabric before geometry checks'}
