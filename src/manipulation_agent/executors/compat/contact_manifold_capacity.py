"""Size the contact-observation buffer for the loaded scene, not a tiny demo.

This does not change PhysX contacts, solver settings, collision filters or any
acceptance threshold. It prevents valid contacts from losing their measured
separations when the upstream scene-wide buffer of 256 points is exhausted.
"""


def install_contact_manifold_capacity():
    from omnigibson.utils.usd_utils import RigidContactAPI

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
            'minimum_points': 4096, 'points_per_reporting_body': 64,
            'allocations': dict(configured._mas_allocations),
            'collision_physics_and_acceptance_thresholds': 'unchanged'}
