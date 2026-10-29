"""Receiving poses for actual convex macro-particle colliders, not outer spheres."""
import math
import time


def convex_sphere_overlap(mesh, equations, centers, radii):
    """Exact sphere distance to a convex mesh, including centers inside it."""
    import numpy as np
    import trimesh

    if not len(centers):
        return False
    inside = (centers @ equations[:, :3].T + equations[:, 3] <= 1e-9).all(axis=1)
    if inside.any():
        return True
    _, distances, _ = trimesh.proximity.closest_point_naive(mesh, centers)
    return bool((distances < np.asarray(radii)).any())


def macro_surface_poses(scene, destination, system, limit, *, diagnostics):
    """Place native particle boxes on a surface when enclosing spheres do not fit."""
    import torch
    from omnigibson.utils import transform_utils as T
    from omnigibson.utils.sampling_utils import sample_cuboid_on_object_full_grid_topdown
    from .particle_view import physical_particle_view

    template = system.particle_object
    points = (torch.as_tensor(template.get_attribute('points'), dtype=torch.float32)
              * template.scale * system.max_scale - system._particle_offset)
    low, high = points.amin(0), points.amax(0)
    extent, box_center = high - low, (high + low) / 2
    surface_low, surface_high = destination.aabb
    rest = float(system.particle_particle_rest_distance)
    spacing = max(rest, math.sqrt(float((surface_high[:2]-surface_low[:2]).prod()) / max(1, limit*4)))
    samples = sample_cuboid_on_object_full_grid_topdown(
        destination, ray_spacing=spacing, cuboid_dimensions=extent,
        cuboid_bottom_padding=.001, undo_cuboid_bottom_padding=False)
    valid = [sample for sample in samples if sample[0] is not None]
    diagnostics.update(method='native_oriented_macro_surface', template_extent_m=extent.tolist(),
                       sphere_radius_m=float(system.particle_radius), candidates=len(samples),
                       native_box_candidates=len(valid), sampling_distance_m=spacing)
    if not valid:
        return _pose_result([], [])
    orientations = torch.stack([sample[2] for sample in valid])
    # The native sampler returns box centers; particle transforms use the
    # enclosing-sphere center. Keep that offset consistent with volume pouring.
    positions = torch.stack([sample[0] for sample in valid]) - T.quat2mat(orientations) @ box_center
    clear = torch.ones(len(positions), dtype=torch.bool, device=positions.device)
    for other in scene.active_systems.values():
        if (not other.n_particles or getattr(other, 'particles_view', None) is not None
                or physical_particle_view(other) is None):
            continue
        # Native box queries already include macro rigid shapes. Only PBD
        # particles, absent from those queries, need this separate sphere test.
        reach = float(system.particle_radius + other.particle_radius)
        centers = other.get_particles_position_orientation()[0]
        near = ((centers >= positions.amin(0)-reach) & (centers <= positions.amax(0)+reach)).all(dim=1)
        for chunk in centers[near].split(512):
            clear &= (torch.cdist(positions, chunk) >= reach).all(dim=1)
    positions, orientations = positions[clear], orientations[clear]
    selected = []
    for index in torch.randperm(len(positions)).tolist():
        if selected and bool(((positions[selected]-positions[index]).norm(dim=1) < rest-1e-7).any()):
            continue
        selected.append(index)
        if len(selected) >= limit:
            break
    return positions[selected], orientations[selected]


def macro_receiving_poses(scene, destination, system, limit, *, diagnostics):
    """Query the same convex mesh geometry used by native macro rigid bodies.

    Borrow the existing, nonphysical visual template for hypothetical queries
    and restore its pose/scale before any physics. Adding or deleting even an
    unregistered USD mesh invalidates articulation views in the pinned engine.
    Micro particles need explicit sphere/mesh tests because PhysX scene queries
    do not include their PBD shapes.
    """
    import numpy as np
    import torch
    import trimesh
    from scipy.spatial import ConvexHull
    from omnigibson import lazy
    from omnigibson.object_states import ContainedParticles
    from omnigibson.utils import transform_utils as T
    from .particle_view import physical_particle_view

    link = destination.states[ContainedParticles].link
    low, high = (x.detach().cpu().numpy() for x in link.visual_aabb)
    template = system.particle_object
    points = (torch.as_tensor(template.get_attribute('points'), dtype=torch.float32)
              * template.scale * system.max_scale - system._particle_offset).cpu().numpy()
    hull = ConvexHull(points)
    mesh = trimesh.Trimesh(vertices=points, faces=hull.simplices, process=False)
    local_low, local_high = points.min(axis=0), points.max(axis=0)
    half = (local_high - local_low) / 2
    box_center = (local_high + local_low) / 2
    radius = float(system.particle_radius)
    rest = float(system.particle_particle_rest_distance)
    diagnostics.update(method='native_convex_macro_geometry', template_extent_m=(2*half).tolist(),
                       sphere_radius_m=radius, queries=0, sphere_rejections=0, passes=[])
    micro_centers, micro_radii = [], []
    for other in scene.active_systems.values():
        if (not other.n_particles or getattr(other, 'particles_view', None) is not None
                or physical_particle_view(other) is None):
            continue
        centers = other.get_particles_position_orientation()[0].detach().cpu().numpy()
        reach = radius + float(other.particle_radius)
        keep = ((centers >= low-reach) & (centers <= high+reach)).all(axis=1)
        micro_centers.extend(centers[keep])
        micro_radii.extend([float(other.particle_radius)] * int(keep.sum()))
    micro_centers = np.asarray(micro_centers).reshape(-1, 3)
    micro_radii = np.asarray(micro_radii)
    query = lazy.omni.physx.get_physx_scene_query_interface()
    template_pose = tuple(x.clone() for x in template.get_position_orientation())
    template_scale = template.scale.clone()
    accepted, accepted_rotations = [], []
    deadline = time.monotonic() + 15.
    try:
        template.scale = template_scale * system.max_scale
        encoded = lazy.pxr.PhysicsSchemaTools.encodeSdfPath(lazy.pxr.Sdf.Path(template.prim_path))
        for euler in ((0., 0., 0.), (math.pi/2, 0., 0.), (0., math.pi/2, 0.),
                      (-math.pi/2, 0., 0.), (0., -math.pi/2, 0.), (math.pi, 0., 0.)):
            quaternion = T.euler2quat(torch.tensor(euler, dtype=torch.float32))
            rotation = T.quat2mat(quaternion).cpu().numpy()
            rotated = points @ rotation.T
            # Geometry bounds determine the possible centers. Particle rest
            # spacing only separates accepted particles, never clips height.
            start, end = low - rotated.min(axis=0) + .0005, high - rotated.max(axis=0) - .0005
            if (start > end).any():
                continue
            step = max(.002, min(.008, float((2*half).min())/3))
            axes = [np.linspace(a, z, max(1, int(math.ceil((z-a)/step))+1)) for a,z in zip(start,end)]
            xy = np.asarray(np.meshgrid(axes[0], axes[1], indexing='ij')).reshape(2, -1).T
            xy = xy[((xy-(low[:2]+high[:2])/2)**2).sum(axis=1).argsort()]
            phase = dict(orientation=quaternion.tolist(), candidates=0, accepted=0)
            diagnostics['passes'].append(phase)
            for z in reversed(axes[2]):
                candidates = np.column_stack((xy, np.full(len(xy), z)))
                inside = link.check_points_in_volume(torch.as_tensor(candidates, dtype=torch.float32))
                for center in candidates[inside.cpu().numpy()]:
                    if time.monotonic() >= deadline:
                        diagnostics['termination'] = 'sampling_budget_exhausted'
                        return _pose_result(accepted, accepted_rotations)
                    if accepted and (np.linalg.norm(np.asarray(accepted)-center, axis=1) < rest).any():
                        continue
                    phase['candidates'] += 1
                    near = np.linalg.norm(micro_centers-center, axis=1) < radius+micro_radii
                    if convex_sphere_overlap(mesh, hull.equations,
                                             (micro_centers[near]-center) @ rotation, micro_radii[near]):
                        diagnostics['sphere_rejections'] += 1
                        continue
                    box_position = center + rotation @ box_center
                    diagnostics['queries'] += 1
                    blocked = query.overlap_box_any(half.tolist(), box_position.tolist(), quaternion.tolist())
                    if blocked:
                        # An enclosing box is only a fast clearance proof.
                        # Its overlap must be refined using the real hull.
                        origin = center - rotation @ system._particle_offset.cpu().numpy()
                        template.set_position_orientation(
                            torch.as_tensor(origin, dtype=torch.float32), quaternion)
                        blocked = query.overlap_mesh_any(*encoded)
                    if not blocked:
                        accepted.append(center.copy())
                        accepted_rotations.append(quaternion.tolist())
                        phase['accepted'] += 1
                        if len(accepted) >= limit:
                            diagnostics['termination'] = 'portion_limit'
                            return _pose_result(accepted, accepted_rotations)
            if accepted:
                diagnostics['termination'] = 'partial_portion'
                return _pose_result(accepted, accepted_rotations)
        diagnostics['termination'] = 'candidate_poses_exhausted'
        return _pose_result(accepted, accepted_rotations)
    finally:
        template.scale = template_scale
        template.set_position_orientation(*template_pose)


def _pose_result(positions, orientations):
    import numpy as np
    import torch
    return (torch.as_tensor(np.asarray(positions), dtype=torch.float32).reshape(-1, 3),
            torch.as_tensor(np.asarray(orientations), dtype=torch.float32).reshape(-1, 4))
