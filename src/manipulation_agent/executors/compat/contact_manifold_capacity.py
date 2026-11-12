"""Size contact observations and avoid caching unused force-vector components.

This does not change PhysX contacts, solver settings, collision filters or any
acceptance threshold. It prevents valid contacts from losing their measured
separations when the upstream scene-wide buffer of 256 points is exhausted.
"""


def install_contact_manifold_capacity():
    from omnigibson.utils.usd_utils import RigidContactAPI, RigidContactAPIImpl

    collect = RigidContactAPIImpl.add_contacts_from_physics_step
    if not getattr(collect, '_mas_compact_contacts', False):
        def compact_contacts(api):
            collect(api)
            # Native aggregation only tests whether each impulse vector has
            # any nonzero component. Keep that exact result in one bool;
            # transforms, net forces and all sleep/contact logic stay native.
            for pending in api._PENDING_IMPULSES.values():
                if pending:
                    pending[-1] = pending[-1].ne(0).any(dim=-1, keepdim=True)

        compact_contacts._mas_compact_contacts = True
        RigidContactAPIImpl.add_contacts_from_physics_step = compact_contacts

    original = RigidContactAPI.get_max_contact_data_count
    if not getattr(original, '_mas_scene_capacity', False):
        allocations = {}

        def capacity(n_bodies):
            count = max(int(original(n_bodies)), 4096, 64 * int(n_bodies))
            allocations[int(n_bodies)] = count
            return count

        capacity._mas_scene_capacity = True
        capacity._mas_allocations = allocations
        RigidContactAPI.get_max_contact_data_count = capacity
    configured = RigidContactAPI.get_max_contact_data_count
    return {'status': 'configured', 'scope': 'contact_observation_buffer_only',
            'pending_impulses': 'one_nonzero_bool_per_native_contact_vector',
            'native_contact_aggregation': 'unchanged',
            'minimum_points': 4096, 'points_per_reporting_body': 64,
            'allocations': dict(configured._mas_allocations),
            'collision_physics_and_acceptance_thresholds': 'unchanged'}
