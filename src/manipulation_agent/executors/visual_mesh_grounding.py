"""Private exact-pixel object ownership; independent of depth backprojection."""
from ..contracts import SkillError


def _ray_intersects_bounds(origin, direction, bounds):
    """Positive-ray slab query against local *visual* geometry bounds."""
    near, far = 0.0, float('inf')
    for axis in range(3):
        lo, hi = bounds[:, axis]
        if abs(direction[axis]) < 1e-12:
            if not lo <= origin[axis] <= hi:
                return False
            continue
        a = (lo - origin[axis]) / direction[axis]
        b = (hi - origin[axis]) / direction[axis]
        near, far = max(near, min(a, b)), min(far, max(a, b))
        if near > far:
            return False
    return True


def query_visual_surface(backend, start, direction):
    """Return the first visible-mesh owner on the exact selected camera ray.

    Depth is deliberately not an input. The caller keeps the depth-backprojected
    point for actuation; this query supplies only the simulator object handle.
    Local rigid meshes are cached, with live transforms for moving links. Cloth
    uses its current vertices. Bounds come from visual geometry, not collision
    AABBs. Robot geometry participates
    so selecting the robot cannot silently select an occluded object behind it.
    """
    import numpy as np
    from omnigibson.utils.usd_utils import mesh_prim_to_trimesh_mesh

    origin = start.cpu().numpy()
    vector = direction.cpu().numpy()
    vector = vector / np.linalg.norm(vector)
    cache = getattr(backend, '_visual_mesh_cache', None)
    if cache is None:
        cache = backend._visual_mesh_cache = {}
    bounds_cache = getattr(backend, '_visual_mesh_bounds_cache', None)
    if bounds_cache is None:
        bounds_cache = backend._visual_mesh_bounds_cache = {}
    best = None
    checked = 0
    objects = list(backend.env.scene.objects)
    if not any(obj is backend.robot for obj in objects):
        objects.append(backend.robot)
    for obj in objects:
        for link in obj.links.values():
            # OG rigid links contain visual meshes; a ClothPrim is itself the
            # deforming visual mesh and has no visual_meshes collection.
            rigid = hasattr(link, 'visual_meshes')
            meshes = link.visual_meshes.values() if rigid else (link,)
            for mesh in meshes:
                if not mesh.visible:
                    continue
                path = mesh.prim_path
                transform = mesh.scaled_transform.cpu().numpy()
                inverse = np.linalg.inv(transform)
                local_origin = (inverse @ np.r_[origin, 1.])[:3]
                local_vector = inverse[:3, :3] @ vector
                if rigid and path in bounds_cache:
                    bounds = bounds_cache[path]
                else:
                    vertices = mesh.points.cpu().numpy()
                    if not len(vertices):
                        continue
                    bounds = np.stack((vertices.min(axis=0), vertices.max(axis=0)))
                    if rigid:
                        bounds_cache[path] = bounds
                # Convert faces/build triangle query data only for meshes whose
                # visual vertex bounds intersect this ray, not the entire scene.
                if not _ray_intersects_bounds(local_origin, local_vector, bounds):
                    continue
                if rigid:
                    if path not in cache:
                        cache[path] = mesh_prim_to_trimesh_mesh(
                            mesh.prim, include_normals=False, include_texcoord=False)
                    geometry = cache[path]
                else:
                    import trimesh
                    geometry = trimesh.Trimesh(vertices=vertices,
                                               faces=mesh.faces.cpu().numpy(), process=False)
                if geometry.is_empty:
                    continue
                locations, _, _ = geometry.ray.intersects_location(
                    [local_origin], [local_vector], multiple_hits=True)
                checked += 1
                if not len(locations):
                    continue
                world = locations @ transform[:3, :3].T + transform[:3, 3]
                distances = (world - origin) @ vector
                positive = np.flatnonzero(distances > 0)
                if not len(positive):
                    continue
                index = int(positive[distances[positive].argmin()])
                distance = float(distances[index])
                if best is None or distance < best[0]:
                    best = distance, obj, path, world[index].tolist()
    if best is None or best[1] is backend.robot:
        exc = SkillError('invalid_visual_target',
                         'Selected ray hits the robot' if best else 'No visual object on selected ray')
        exc.diagnostics = {'reason': 'robot_surface' if best else 'no_visual_surface',
                           'meshes_tested': checked}
        raise exc
    distance, obj, path, hit = best
    return obj, {'visual_mesh': path, 'visual_triangle_position': hit,
                 'visual_ray_distance_m': distance, 'visual_meshes_tested': checked,
                 'method': 'first_visual_surface_on_selected_ray',
                 'depth_consistency_check': False}
