"""Read micro-particle quaternions in bulk and preserve their USD ordering."""


def get_particle_orientations(instancer):
    import torch
    from omnigibson.utils.numpy_utils import vtarray_to_torch

    values = instancer.get_attribute('orientations')
    # Vt's array buffer is already XYZW. Avoid a Python loop per particle
    # every time a native state queries particle poses; retain the empty ABI.
    return vtarray_to_torch(values) if len(values) else torch.empty(0, dtype=torch.float32)


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
    if current.fget is not get_particle_orientations or current.fset is not set_particle_orientations:
        PhysxParticleInstancer.particle_orientations = current.getter(get_particle_orientations).setter(set_particle_orientations)
    return {'status': 'configured', 'scope': 'process_local_micro_particle_orientation_accessors',
            'input': 'xyzw', 'usd_encoding': 'explicit_Gf_Quath_scalar_and_imaginary_vector',
            'read_conversion': 'native_Vt_XYZW_array_buffer',
            'particle_count_positions_velocities_and_materials': 'unchanged'}
