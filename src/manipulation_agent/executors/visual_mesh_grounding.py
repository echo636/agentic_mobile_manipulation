"""Private ownership at the selected pixel's backprojected surface point."""
from ..contracts import SkillError
from types import SimpleNamespace


def query_visual_surface(backend, point, *, reject_particles=False, camera_position=None,
                         renderer_object=None):
    """Find the rendered mesh surface at the measured world-space point.

    Do not cast another ray, replace the point, search neighboring pixels, or
    reject selections with a depth/mesh distance threshold. Bounds only supply
    a conservative lower bound for accelerating the exact surface query.
    Numerically coincident surfaces use optional same-capture instance ownership,
    falling back to camera order within float32 roundoff.
    """
    import itertools
    import numpy as np
    import trimesh
    import omnigibson.lazy as lazy
    from omnigibson.utils.usd_utils import mesh_prim_to_trimesh_mesh

    world_point = point.detach().cpu().numpy().astype(float)
    # Depth and world transforms are float32. Coincident surfaces (e.g. a thin
    # box bottom resting on a table) can differ by less than one rounding unit.
    # Absolute distance then selects the table underneath a visibly selected
    # box. Only within numerical precision, resolve ownership toward the camera;
    # the measured point stays unchanged and no additional ray is cast.
    toward_camera = None
    roundoff = 0.
    if camera_position is not None:
        camera = camera_position.detach().cpu().numpy().astype(float)
        vector = camera - world_point
        length = float(np.linalg.norm(vector))
        if np.isfinite(camera).all() and np.isfinite(length) and length > 0:
            toward_camera = vector / length
            roundoff = 4 * np.finfo(np.float32).eps * max(1., float(np.abs(world_point).max()),
                                                        float(np.abs(camera).max()))
    cache = getattr(backend, '_visual_mesh_cache', None)
    if cache is None:
        cache = backend._visual_mesh_cache = {}
    bounds_cache = getattr(backend, '_visual_mesh_bounds_cache', None)
    if bounds_cache is None:
        bounds_cache = backend._visual_mesh_bounds_cache = {}
    candidates = []
    guides_skipped = 0
    invalid_meshes = 0
    invalid_triangles = 0
    invalid_surface_points = 0
    objects = list(backend.env.scene.objects)
    if not any(obj is backend.robot for obj in objects):
        objects.append(backend.robot)
    particle_owners = {}
    particle_transforms = {}
    particle_geometry = {}
    attached_surfaces = {}
    live_objects = {id(obj) for obj in objects}
    for system in getattr(backend.env.scene, 'active_systems', {}).values():
        particles = getattr(system, 'particles', None)
        attachments = getattr(system, '_particles_info', None)
        if not particles or not attachments:
            continue
        # MacroVisual particles (dust, stains) are rendered meshes attached to
        # a specific object/link. They are absent from link.visual_meshes.
        # Include those exact surfaces, with their recorded owner, in the same
        # spatial query. Ignoring them can assign a dust pixel to an unrelated
        # nearby object. Neither the measured point nor the selected pixel moves.
        meshes = {}
        for name, mesh in particles.items():
            parent = attachments.get(name, {}).get('obj')
            if id(parent) not in live_objects:
                continue
            meshes[name] = mesh
            attached_surfaces[mesh.prim_path] = (parent, system.name)
            particle_geometry[mesh.prim_path] = system.particle_object
        if meshes:
            objects.append(SimpleNamespace(links={'particles': SimpleNamespace(visual_meshes=meshes)}))
    if reject_particles:
        for system in getattr(backend.env.scene, 'active_systems', {}).values():
            particles = getattr(system, 'particles', None)
            view = getattr(system, 'particles_view', None)
            if not particles or view is None:
                continue
            # Macro particles have actual rendered meshes. Include their exact
            # surfaces in the same nearest-surface query, without a radius
            # threshold or redirecting the pixel to a container/background.
            owner = SimpleNamespace(links={'particles': SimpleNamespace(visual_meshes=particles)})
            objects.append(owner)
            particle_owners[id(owner)] = system
            import omnigibson.utils.transform_utils as T
            raw = view.get_transforms()
            rotations = T.quat2mat(raw[:, 3:]).detach().cpu().numpy()
            by_path = {mesh.prim_path: mesh for mesh in particles.values()}
            for i, path in enumerate(view.prim_paths):
                mesh = by_path[path]
                transform = np.eye(4)
                transform[:3, :3] = rotations[i] * mesh.scale.detach().cpu().numpy()
                transform[:3, 3] = raw[i, :3].detach().cpu().numpy()
                particle_transforms[path] = transform
                particle_geometry[path] = system.particle_object
    for obj in objects:
        for link in obj.links.values():
            rigid = hasattr(link, 'visual_meshes')
            for mesh in (link.visual_meshes.values() if rigid else (link,)):
                if not mesh.visible:
                    continue
                purpose = str(lazy.pxr.UsdGeom.Imageable(mesh.prim).ComputePurpose())
                if purpose == 'guide':
                    guides_skipped += 1
                    continue
                path = mesh.prim_path
                source_mesh = particle_geometry.get(path, mesh)
                cache_path = source_mesh.prim_path
                vertices = None
                if rigid and cache_path in bounds_cache:
                    bounds = bounds_cache[cache_path]
                else:
                    vertices = source_mesh.points.detach().cpu().numpy()
                    finite_vertices = vertices[np.isfinite(vertices).all(axis=1)]
                    if not len(finite_vertices):
                        invalid_meshes += 1
                        continue
                    bounds = np.stack((finite_vertices.min(axis=0), finite_vertices.max(axis=0)))
                    if rigid:
                        bounds_cache[cache_path] = bounds
                transform = (particle_transforms[path] if path in particle_transforms else
                             mesh.scaled_transform.detach().cpu().numpy().astype(float))
                if not np.isfinite(transform).all():
                    invalid_meshes += 1
                    continue
                corners = np.array(list(itertools.product(*bounds.T)))
                corners = corners @ transform[:3, :3].T + transform[:3, 3]
                lo, hi = corners.min(axis=0), corners.max(axis=0)
                lower_bound = float(np.linalg.norm(np.maximum(np.maximum(lo-world_point, world_point-hi), 0.)))
                owner = attached_surfaces[path][0] if path in attached_surfaces else obj
                candidates.append((lower_bound, owner, mesh, rigid, vertices, transform, purpose))
    best = None
    coincident = []
    checked = 0
    for lower_bound, obj, mesh, rigid, vertices, transform, purpose in sorted(candidates, key=lambda row: row[0]):
        if best is not None and lower_bound > max(best[0], roundoff) + 1e-9:
            break
        if rigid:
            source_mesh = particle_geometry.get(mesh.prim_path, mesh)
            if source_mesh.prim_path not in cache:
                cache[source_mesh.prim_path] = mesh_prim_to_trimesh_mesh(
                    source_mesh.prim, include_normals=False, include_texcoord=False)
            geometry = cache[source_mesh.prim_path]
        else:
            geometry = trimesh.Trimesh(vertices=vertices, faces=mesh.faces.cpu().numpy(), process=False)
        if geometry.is_empty:
            continue
        # World-space distances remain correct under nonuniform asset scale.
        triangles = geometry.triangles @ transform[:3, :3].T + transform[:3, 3]
        # Real assets contain collapsed faces. They do not define a rendered
        # surface, and closest_point may divide by zero for them. Never let a
        # NaN win argmin and poison the best object's distance for later meshes.
        finite = np.isfinite(triangles).all(axis=(1, 2))
        invalid_triangles += int((~finite).sum())
        triangles = triangles[finite]
        area = np.linalg.norm(np.cross(triangles[:, 1] - triangles[:, 0],
                                      triangles[:, 2] - triangles[:, 0]), axis=1)
        valid = np.isfinite(area) & (area > 0)
        invalid_triangles += int((~valid).sum())
        triangles = triangles[valid]
        checked += 1
        if not len(triangles):
            continue
        with np.errstate(divide='ignore', invalid='ignore'):
            nearest = trimesh.triangles.closest_point(
                triangles, np.broadcast_to(world_point, (len(triangles), 3)))
        distances = np.linalg.norm(nearest-world_point, axis=1)
        finite = np.isfinite(distances)
        invalid_surface_points += int((~finite).sum())
        if not finite.any():
            continue
        distances = np.where(finite, distances, np.inf)
        index = int(distances.argmin())
        distance = float(distances[index])
        candidate = distance, obj, mesh.prim_path, nearest[index].tolist(), purpose
        if distance <= roundoff and toward_camera is not None:
            coincident.append(candidate)
        if best is None or distance < best[0]:
            best = candidate
    resolution = None
    if len(coincident) > 1:
        # Optional same-capture instance ownership resolves numerical overlap
        # without moving the depth point or selecting a neighboring surface.
        # A label outside this geometric tie cannot redirect the operation.
        rendered = [row for row in coincident if row[1] is renderer_object]
        if rendered:
            best = min(rendered, key=lambda row: (row[0], row[2]))
            resolution = 'same_frame_renderer_instance'
        else:
            best = min(coincident, key=lambda row: (
                -float(np.dot(np.asarray(row[3])-world_point, toward_camera)), row[0], row[2]))
            resolution = 'camera_frontmost'
    numerical_diagnostics = {'invalid_meshes_skipped': invalid_meshes,
                             'invalid_triangles_skipped': invalid_triangles,
                             'invalid_surface_points_skipped': invalid_surface_points,
                             'coincident_surface_candidates': len(coincident),
                             'coincident_surface_roundoff_m': roundoff,
                             'coincident_surface_resolution': resolution}
    if best is None or best[1] is backend.robot:
        exc = SkillError('invalid_visual_target',
                         'Selected surface belongs to the robot' if best else 'No visible mesh surface')
        exc.diagnostics = {'reason': 'robot_surface' if best else 'no_visual_surface',
                           'meshes_tested': checked, 'guide_meshes_skipped': guides_skipped,
                           **numerical_diagnostics}
        raise exc
    distance, obj, path, nearest, purpose = best
    if id(obj) in particle_owners:
        exc = SkillError('particle_target', 'Selected surface belongs to loose physical particles')
        exc.diagnostics = {'reason': 'physical_particle_surface',
                           'particle_system': particle_owners[id(obj)].name,
                           'visual_mesh': path, 'visual_triangle_position': nearest,
                           'surface_point_distance_m': distance, 'visual_meshes_tested': checked,
                           **numerical_diagnostics}
        raise exc
    return obj, {'visual_mesh': path, 'visual_triangle_position': nearest,
                 'visual_mesh_purpose': purpose, 'guide_meshes_skipped': guides_skipped,
                 'surface_point_distance_m': distance, 'visual_meshes_tested': checked,
                 'method': 'nearest_visual_surface_to_backprojected_point',
                 'surface_kind': 'attached_material' if path in attached_surfaces else 'object_mesh',
                 'material_system': attached_surfaces[path][1] if path in attached_surfaces else None,
                 'depth_consistency_check': False, **numerical_diagnostics}
