"""Preserve existing physical macro particles during controlled payload motion.

Capture annotated containment, or upward-facing collision support when requested.
Stable PhysX prim paths identify particles across view rebuilds.
Released particles remain physical; no material is generated or replenished.
"""
from dataclasses import dataclass
import math

from ..contracts import SkillError


@dataclass(frozen=True)
class ParticlePayload:
    container: object
    link: object
    system: object
    paths: tuple
    positions: object
    orientations: object
    relation: str = 'contained'
    supports: tuple = ()


def _support_at(path, start, vector):
    """First opposing collision along a saved support probe."""
    from omnigibson.utils.sampling_utils import raytest
    import torch
    if not bool(torch.isfinite(start).all() and torch.isfinite(vector).all()):
        return None
    hit = raytest(start, start + vector, ignore_bodies=(path,), ignore_collisions=(path,))
    if not hit['hit']:
        return None
    facing = -sum(float(n) * float(d) for n, d in zip(hit['normal'], vector))
    return hit['rigidBody'] if math.isfinite(facing) and facing > 0 else None


def _particle_supports(system, path, center):
    """Sample the lower contact hemisphere, including off-center pile contacts.

    Ignore only the particle itself: a bowl, table or other intervening body
    must occlude a board below it. Bounding-box proximity alone is not support.
    """
    from omnigibson.object_states.contact_particles import m
    radius = float(system.particle_contact_radius)
    if not math.isfinite(radius) or radius <= 0:
        return ()
    offsets = [(0., 0.)] + [(radius * .75 * math.cos(i * math.pi / 4),
                            radius * .75 * math.sin(i * math.pi / 4)) for i in range(8)]
    supports = {}
    for x, y in offsets:
        offset = center.new_tensor([x, y, 0.])
        # Keep the probes inside the contact sphere, not a rectangular slab.
        depth = math.sqrt((radius + m.CONTACT_TOLERANCE)**2 - x*x - y*y)
        vector = center.new_tensor([0., 0., -depth])
        support = _support_at(path, center + offset, vector)
        if support is not None and support not in supports:
            supports[support] = (support, offset, vector)
    return tuple(supports.values())


def _supported_particles(systems, containers, owners):
    """Resolve support from bottom to top, including piles of different materials.

    A node can follow only a selected rigid link or an already owned lower
    particle. No category names, task bindings, or desired destinations are used.
    """
    import torch
    links = {link.prim_path: (obj, link) for obj in containers
             for link in obj.links.values() if not link.is_meta_link}
    if not links:
        return []
    bounds = [obj.aabb for obj in containers]
    nodes = []
    for system in systems:
        centers, _ = system.get_particles_position_orientation()
        radius = float(system.particle_contact_radius)
        if not math.isfinite(radius) or radius <= 0:
            continue
        near = torch.zeros(len(centers), dtype=torch.bool, device=centers.device)
        for lo, hi in bounds:
            near |= (((centers[:, :2] >= lo[:2] - radius) &
                      (centers[:, :2] <= hi[:2] + radius)).all(dim=1) &
                     (centers[:, 2] >= lo[2] - radius))
        near &= torch.isfinite(centers).all(dim=1)
        for i in near.nonzero().flatten().tolist():
            path = system.particles_view.prim_paths[i]
            nodes.append((float(centers[i, 2]), path, system, i, centers[i]))
    heights = {path: z for z, path, _, _, _ in nodes}
    selected = []
    for z, path, system, index, center in sorted(nodes, key=lambda node: (node[0], node[1])):
        if path in owners:
            continue
        for support in _particle_supports(system, path, center):
            parent = support[0]
            owner = links.get(parent)
            if owner is None and heights.get(parent, math.inf) < z:
                owner = owners.get(parent)
            if owner is not None:
                owners[path] = owner
                selected.append((owner, system, index, support))
                break
    return selected


def _payload(container, link, system, indices, relation='contained', supports=()):
    import omnigibson.utils.transform_utils as T
    parent_pos, parent_quat = link.get_position_orientation()
    rotation = T.quat2mat(parent_quat)
    transforms = system.particles_view.get_transforms()[indices].clone()
    return ParticlePayload(
        container, link, system, tuple(system.particles_view.prim_paths[i] for i in indices),
        (transforms[:, :3] - parent_pos) @ rotation,
        T.quat_multiply(T.quat_conjugate(parent_quat), transforms[:, 3:].T).T,
        relation, tuple((parent, offset @ rotation, vector @ rotation) for parent, offset, vector in supports))


def snapshot_particles(scene, containers, *, include_supported=False):
    systems = [system for system in getattr(scene, 'active_systems', {}).values()
               if getattr(system, 'particles_view', None) is not None and system.n_particles]
    if not systems:
        return []
    from omnigibson.object_states import ContainedParticles
    payload = []
    owners = {}
    # Children first: a bag nested in a box owns its contents, not the box.
    for container in reversed(containers):
        if ContainedParticles not in container.states:
            continue
        link = container.states[ContainedParticles].link
        for system in systems:
            view = system.particles_view
            paths = tuple(view.prim_paths)
            centers, _ = system.get_particles_position_orientation()
            inside = link.check_points_in_volume(centers)
            indices = [i for i, path in enumerate(paths)
                       if bool(inside[i]) and path not in owners]
            if not indices:
                continue
            owners.update((paths[i], (container, link)) for i in indices)
            payload.append(_payload(container, link, system, indices))
    if include_supported:
        groups = {}
        for (container, link), system, index, support in _supported_particles(systems, containers, owners):
            key = (id(container), id(link), id(system))
            group = groups.setdefault(key, (container, link, system, [], []))
            group[3].append(index)
            group[4].append(support)
        for container, link, system, indices, supports in groups.values():
            payload.append(_payload(container, link, system, indices, 'supported', supports))
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
        if record.relation == 'supported':
            import omnigibson.utils.transform_utils as T
            rotation = T.quat2mat(record.link.get_position_orientation()[1])
            # Rotate the original contact probes with the assembly. Re-sampling
            # a world-aligned grid after a yaw can miss a valid corner contact.
            if any(_support_at(path, centers[index] + offset @ rotation.T, vector @ rotation.T) != parent
                   for path, index, (parent, offset, vector) in zip(record.paths, indices, record.supports)):
                raise SkillError('postcondition_error', 'Carried particles lost their surface support', changed=True)
        elif not bool(record.link.check_points_in_volume(centers[indices]).all()):
            raise SkillError('postcondition_error', 'Contained particles left the carried container', changed=True)


def particle_count(payload, relation=None):
    return sum(len(record.paths) for record in payload if relation is None or record.relation == relation)


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
