"""Receiving poses for actual convex macro-particle colliders, not outer spheres."""
import math
import time
import uuid


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


def macro_receiving_poses(scene, destination, system, limit, *, diagnostics):
    """Query the same convex mesh geometry used by native macro rigid bodies.

    A temporary invisible mesh supplies hypothetical poses to PhysX queries.
    It has no collision/rigid-body API, never enters an object registry, and is
    removed before physics advances. Micro particles need explicit sphere/mesh
    tests because PhysX scene queries do not include their PBD shapes.
    """
    import numpy as np
    import torch
    import trimesh
    from scipy.spatial import ConvexHull
    import omnigibson as og
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
    proxy_path = '/World/mas_material_query_' + uuid.uuid4().hex
    accepted, accepted_rotations = [], []
    deadline = time.monotonic() + 15.
    try:
        with og.sim.editing_usd():
            proxy = lazy.pxr.UsdGeom.Mesh.Define(og.sim.stage, proxy_path)
            proxy.CreatePointsAttr().Set(points.tolist())
            proxy.CreateFaceVertexCountsAttr().Set([3] * len(hull.simplices))
            proxy.CreateFaceVertexIndicesAttr().Set(hull.simplices.flatten().tolist())
            proxy.CreateVisibilityAttr().Set('invisible')
            transform = lazy.pxr.UsdGeom.Xformable(proxy.GetPrim())
            translate = transform.AddTranslateOp()
            orient = transform.AddOrientOp(lazy.pxr.UsdGeom.XformOp.PrecisionFloat)
        encoded = lazy.pxr.PhysicsSchemaTools.encodeSdfPath(lazy.pxr.Sdf.Path(proxy_path))
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
                        with og.sim.editing_usd():
                            translate.Set(lazy.pxr.Gf.Vec3d(*map(float, center)))
                            x,y,qz,w = map(float, quaternion.tolist())
                            orient.Set(lazy.pxr.Gf.Quatf(w, lazy.pxr.Gf.Vec3f(x,y,qz)))
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
        with og.sim.editing_usd():
            og.sim.stage.RemovePrim(proxy_path)


def _pose_result(positions, orientations):
    import numpy as np
    import torch
    return (torch.as_tensor(np.asarray(positions), dtype=torch.float32).reshape(-1, 3),
            torch.as_tensor(np.asarray(orientations), dtype=torch.float32).reshape(-1, 4))
