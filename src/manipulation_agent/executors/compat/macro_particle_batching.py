"""Refresh native physics views once per bulk macro-particle edit."""
from contextlib import contextmanager
from functools import wraps


@contextmanager
def _batch_handles(system):
    import omnigibson as og

    sim = og.sim
    refresh = sim.update_handles
    set_poses = system.set_particles_position_orientation
    previous_refresh = vars(sim).get('update_handles')
    previous_setter = vars(system).get('set_particles_position_orientation')
    dirty = False

    def defer_refresh():
        nonlocal dirty
        dirty = True

    def flush():
        nonlocal dirty
        if dirty:
            dirty = False
            refresh()

    def write_poses(*args, **kwargs):
        # Native generate_particles writes poses before returning, then writes
        # velocities. Its tensor views must already include the whole batch.
        flush()
        return set_poses(*args, **kwargs)

    sim.update_handles = defer_refresh
    system.set_particles_position_orientation = write_poses
    try:
        yield
    finally:
        if previous_setter is None:
            del system.set_particles_position_orientation
        else:
            system.set_particles_position_orientation = previous_setter
        if previous_refresh is None:
            del sim.update_handles
        else:
            sim.update_handles = previous_refresh
        # Removal has no pose write. Also refresh after a partial native edit
        # raises, so the caller never receives stale physics views.
        flush()


def _batched(original):
    @wraps(original)
    def run(system, *args, **kwargs):
        with _batch_handles(system):
            return original(system, *args, **kwargs)
    return run


def install_macro_particle_batching():
    from omnigibson.systems.macro_particle_system import MacroPhysicalParticleSystem

    cls = MacroPhysicalParticleSystem
    if not getattr(cls.generate_particles, '_mas_batch_handles', False):
        cls.generate_particles = _batched(cls.generate_particles)
        cls.generate_particles._mas_batch_handles = True
        cls.remove_particles = _batched(cls.remove_particles)
    return dict(status='installed', scope='native_macro_particle_bulk_add_remove',
                refresh='once_per_batch_before_pose_and_velocity_writes',
                particle_creation_removal_geometry_and_physics='native_unchanged')
