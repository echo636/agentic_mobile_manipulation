"""Repair the pinned macro-particle center/frame pose conversion."""
import hashlib
from importlib import metadata
from pathlib import Path


def set_particle_centers(system, positions=None, orientations=None):
    import torch
    from omnigibson.utils import transform_utils as T

    if system.n_particles == 0:
        return
    if positions is None or orientations is None:
        current_positions, current_orientations = system.get_particles_position_orientation()
        positions = current_positions if positions is None else positions
        orientations = current_orientations if orientations is None else orientations
    # The public getter and serialized state contain bounding-sphere centers.
    # PhysX transforms contain mesh origins. Convert for BOTH a complete pose
    # and a partial pose update; rotation-only updates preserve the center.
    origins = positions - T.quat2mat(orientations) @ system._particle_offset
    transforms = torch.cat((origins, orientations), dim=1)
    system.particles_view.set_transforms(
        transforms, indices=torch.arange(len(transforms), device=transforms.device))


def set_particle_center(system, idx, position=None, orientation=None):
    import torch
    from omnigibson.utils import transform_utils as T

    if not 0 <= idx < system.n_particles:
        raise IndexError('Macro particle index is outside the current particle array')
    if position is None or orientation is None:
        current_position, current_orientation = system.get_particle_position_orientation(idx)
        position = current_position if position is None else position
        orientation = current_orientation if orientation is None else orientation
    origin = position - T.quat2mat(orientation) @ system._particle_offset
    transform = torch.cat((origin, orientation)).reshape(1, -1)
    system.particles_view.set_transforms(
        transform, indices=torch.tensor([idx], device=transform.device))


def install_macro_particle_pose_repair():
    versions = {name: metadata.version(name) for name in ('omnigibson', 'isaacsim')}
    if (versions['omnigibson'], versions['isaacsim']) != ('3.9.2', '5.1.0.0'):
        return dict(status='not_applicable', versions=versions)
    from omnigibson.systems import macro_particle_system as module

    cls = module.MacroPhysicalParticleSystem
    cls.set_particles_position_orientation = set_particle_centers
    cls.set_particle_position_orientation = set_particle_center
    return dict(status='installed', versions=versions,
        repair='macro_particle_sphere_center_to_mesh_origin',
        scope='process_local_macro_particle_pose_setters',
        upstream_path=module.__file__,
        upstream_sha256=hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest(),
        particle_geometry_counts_velocities_and_identities='unchanged')
