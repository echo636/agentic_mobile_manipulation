"""Preserve micro-particle quaternions across the pinned USD serialization API."""


def set_particle_orientations(instancer, quaternions):
    from omnigibson import lazy

    assert quaternions.shape[0] == instancer.n_particles, 'Particle orientation count changed'
    # Torch states use xyzw. Gf's explicit constructor uses a scalar and an
    # imaginary vector; unlike FromNumpy this does not depend on array layout.
    # The pinned native setter permutes xyzw for an API that already uses xyzw,
    # so every load_state otherwise rotates each particle's quaternion fields.
    values = [lazy.pxr.Gf.Quath(float(w), lazy.pxr.Gf.Vec3h(float(x), float(y), float(z)))
              for x, y, z, w in quaternions.detach().cpu().tolist()]
    instancer.set_attribute(attr='orientations', val=lazy.pxr.Vt.QuathArray(values))


def install_particle_quaternion_serialization():
    from omnigibson.systems.micro_particle_system import PhysxParticleInstancer

    current = PhysxParticleInstancer.particle_orientations
    if current.fset is not set_particle_orientations:
        PhysxParticleInstancer.particle_orientations = current.setter(set_particle_orientations)
    return {'status': 'configured', 'scope': 'process_local_micro_particle_orientation_setter',
            'input': 'xyzw', 'usd_encoding': 'explicit_Gf_Quath_scalar_and_imaginary_vector',
            'particle_count_positions_velocities_and_materials': 'unchanged'}
