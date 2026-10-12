"""Preserve contained physical macro particles during controlled container motion.

Only existing particles inside annotated fillable volumes are captured. Stable
PhysX prim paths, rather than array indices, identify them across view rebuilds.
Released particles remain physical; no material is generated or replenished.
"""
from dataclasses import dataclass

from ..contracts import SkillError


@dataclass(frozen=True)
class ParticlePayload:
    container: object
    link: object
    system: object
    paths: tuple
    positions: object
    orientations: object


def snapshot_particles(scene, containers):
    systems = [system for system in getattr(scene, 'active_systems', {}).values()
               if getattr(system, 'particles_view', None) is not None and system.n_particles]
    if not systems:
        return []
    import omnigibson.utils.transform_utils as T
    from omnigibson.object_states import ContainedParticles
    payload = []
    claimed = set()
    # Children first: a bag nested in a box owns its contents, not the box.
    for container in reversed(containers):
        if ContainedParticles not in container.states:
            continue
        link = container.states[ContainedParticles].link
        parent_pos, parent_quat = link.get_position_orientation()
        for system in systems:
            view = system.particles_view
            paths = tuple(view.prim_paths)
            centers, _ = system.get_particles_position_orientation()
            inside = link.check_points_in_volume(centers)
            indices = [i for i, path in enumerate(paths)
                       if bool(inside[i]) and (id(system), path) not in claimed]
            if not indices:
                continue
            transforms = view.get_transforms()[indices].clone()
            selected = tuple(paths[i] for i in indices)
            claimed.update((id(system), path) for path in selected)
            payload.append(ParticlePayload(
                container, link, system, selected,
                (transforms[:, :3] - parent_pos) @ T.quat2mat(parent_quat),
                T.quat_multiply(T.quat_conjugate(parent_quat), transforms[:, 3:].T).T))
    return payload


def _indices(scene, record):
    system = record.system
    if getattr(scene, 'active_systems', {}).get(system.name) is not system or system.particles_view is None:
        raise SkillError('target_changed', 'Carried particle system changed', changed=True)
    indices = {path: i for i, path in enumerate(system.particles_view.prim_paths)}
    if any(path not in indices for path in record.paths):
        raise SkillError('target_changed', 'Carried particles changed; old particles cannot be restored', changed=True)
    return [indices[path] for path in record.paths]


def relocate_particles(scene, payload):
    if not payload:
        return
    import torch
    import omnigibson.utils.transform_utils as T
    # Validate all identities before changing any particle pose.
    selected = [(record, _indices(scene, record)) for record in payload]
    for record, indices in selected:
        view = record.system.particles_view
        position, orientation = record.link.get_position_orientation()
        transforms = view.get_transforms().clone()
        transforms[indices, :3] = record.positions @ T.quat2mat(orientation).T + position
        transforms[indices, 3:] = T.quat_multiply(orientation, record.orientations.T).T
        velocities = view.get_velocities().clone()
        velocities[indices] = 0
        # Use raw rigid-body frames. OG 3.9.2's macro pose getter returns
        # bounding-sphere centers; its setter has asymmetric offset handling.
        # Mixing those conventions silently displaces non-centered particles.
        all_indices = torch.arange(len(transforms), device=transforms.device)
        view.set_transforms(transforms, indices=all_indices)
        view.set_velocities(velocities, indices=all_indices)


def verify_particles(scene, payload):
    for record in payload:
        indices = _indices(scene, record)
        centers, _ = record.system.get_particles_position_orientation()
        if not bool(record.link.check_points_in_volume(centers[indices]).all()):
            raise SkillError('postcondition_error', 'Contained particles left the carried container', changed=True)


def particle_count(payload):
    return sum(len(record.paths) for record in payload)


def snapshot_particle_state(sim):
    """Supplement OG snapshots with raw macro frames (not sphere centers)."""
    records = []
    for scene in getattr(sim, 'scenes', ()) or ():
        for system in getattr(scene, 'active_systems', {}).values():
            view = getattr(system, 'particles_view', None)
            if view is not None and system.n_particles:
                records.append((scene, system, tuple(view.prim_paths),
                                view.get_transforms().clone(), view.get_velocities().clone()))
    return records


def particle_state_unchanged(records):
    return all(scene.active_systems.get(system.name) is system and system.particles_view is not None
               and set(system.particles_view.prim_paths) == set(paths)
               for scene, system, paths, _, _ in records)


def restore_particle_state(records):
    if not records:
        return
    import torch
    if not particle_state_unchanged(records):
        raise SkillError('target_changed', 'Cannot restore changed particle identities', changed=True)
    for _, system, paths, transforms, velocities in records:
        view = system.particles_view
        old_index = {path: i for i, path in enumerate(paths)}
        order = [old_index[path] for path in view.prim_paths]
        indices = torch.arange(len(order), device=transforms.device)
        view.set_transforms(transforms[order], indices=indices)
        view.set_velocities(velocities[order], indices=indices)
