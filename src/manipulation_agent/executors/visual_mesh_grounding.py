"""Private ownership at the selected pixel's backprojected surface point."""
from ..contracts import SkillError


def query_visual_surface(backend, point):
    """Find the rendered mesh surface nearest the measured world-space point.

    Do not cast another ray, replace the point, search neighboring pixels, or
    reject selections with a depth/mesh distance threshold. Bounds only supply
    a conservative lower bound for accelerating the exact surface query.
    """
    import itertools
    import numpy as np
    import trimesh
    import omnigibson.lazy as lazy
    from omnigibson.utils.usd_utils import mesh_prim_to_trimesh_mesh

    world_point = point.detach().cpu().numpy().astype(float)
    cache = getattr(backend, '_visual_mesh_cache', None)
    if cache is None:
        cache = backend._visual_mesh_cache = {}
    bounds_cache = getattr(backend, '_visual_mesh_bounds_cache', None)
    if bounds_cache is None:
        bounds_cache = backend._visual_mesh_bounds_cache = {}
    candidates = []
    guides_skipped = 0
    objects = list(backend.env.scene.objects)
    if not any(obj is backend.robot for obj in objects):
        objects.append(backend.robot)
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
                vertices = None
                if rigid and path in bounds_cache:
                    bounds = bounds_cache[path]
                else:
                    vertices = mesh.points.detach().cpu().numpy()
                    if not len(vertices):
                        continue
                    bounds = np.stack((vertices.min(axis=0), vertices.max(axis=0)))
                    if rigid:
                        bounds_cache[path] = bounds
                transform = mesh.scaled_transform.detach().cpu().numpy().astype(float)
                corners = np.array(list(itertools.product(*bounds.T)))
                corners = corners @ transform[:3, :3].T + transform[:3, 3]
                lo, hi = corners.min(axis=0), corners.max(axis=0)
                lower_bound = float(np.linalg.norm(np.maximum(np.maximum(lo-world_point, world_point-hi), 0.)))
                candidates.append((lower_bound, obj, mesh, rigid, vertices, transform, purpose))
    best = None
    checked = 0
    for lower_bound, obj, mesh, rigid, vertices, transform, purpose in sorted(candidates, key=lambda row: row[0]):
        if best is not None and lower_bound > best[0] + 1e-9:
            break
        if rigid:
            if mesh.prim_path not in cache:
                cache[mesh.prim_path] = mesh_prim_to_trimesh_mesh(
                    mesh.prim, include_normals=False, include_texcoord=False)
            geometry = cache[mesh.prim_path]
        else:
            geometry = trimesh.Trimesh(vertices=vertices, faces=mesh.faces.cpu().numpy(), process=False)
        if geometry.is_empty:
            continue
        # World-space distances remain correct under nonuniform asset scale.
        triangles = geometry.triangles @ transform[:3, :3].T + transform[:3, 3]
        nearest = trimesh.triangles.closest_point(
            triangles, np.broadcast_to(world_point, (len(triangles), 3)))
        distances = np.linalg.norm(nearest-world_point, axis=1)
        index = int(distances.argmin())
        distance = float(distances[index])
        checked += 1
        if best is None or distance < best[0]:
            best = distance, obj, mesh.prim_path, nearest[index].tolist(), purpose
    if best is None or best[1] is backend.robot:
        exc = SkillError('invalid_visual_target',
                         'Selected surface belongs to the robot' if best else 'No visible mesh surface')
        exc.diagnostics = {'reason': 'robot_surface' if best else 'no_visual_surface',
                           'meshes_tested': checked, 'guide_meshes_skipped': guides_skipped}
        raise exc
    distance, obj, path, nearest, purpose = best
    return obj, {'visual_mesh': path, 'visual_triangle_position': nearest,
                 'visual_mesh_purpose': purpose, 'guide_meshes_skipped': guides_skipped,
                 'surface_point_distance_m': distance, 'visual_meshes_tested': checked,
                 'method': 'nearest_visual_surface_to_backprojected_point',
                 'depth_consistency_check': False}
