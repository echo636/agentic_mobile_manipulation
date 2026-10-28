"""Conservative door/lid clearance without advancing the live physics world."""


def collision_piece_enclosures(vertices, transform):
    """Enclose the same mesh in its authored axes and its principal axes.

    Principal axes can tilt a thin door's box below the real mesh and into the
    floor. An obstacle must overlap both enclosing boxes to remain unresolved:
    clearance from either enclosure proves clearance from the enclosed mesh.
    """
    import numpy as np
    from .placement_overlap import oriented_piece_box

    u, _, vh = np.linalg.svd(transform[:3, :3])
    axes = u @ vh
    if np.linalg.det(axes) < 0:
        axes[:, -1] *= -1
    local = vertices @ axes
    low, high = local.min(axis=0), local.max(axis=0)
    authored = ((low + high) / 2 @ axes.T, np.maximum((high - low) / 2, 1e-6), axes)
    principal = oriented_piece_box(vertices)
    return [('authored_axes', authored), *([('principal_axes', principal)] if principal is not None else [])]


def moving_link_clearance(backend, links, moving, ignored, check):
    """Certify an endpoint using live scene queries at current collider poses.

    Each box encloses one collision mesh, not the whole object. A hit in every
    enclosure means the proposal is uncertified, not that the meshes necessarily
    intersect. This deliberately avoids stepping a hypothetical interpenetrating
    articulation: restoring body state cannot repair a poisoned CUDA context.
    """
    import numpy as np
    import omnigibson.lazy as lazy
    from omnigibson.utils import transform_utils as T

    query = lazy.omni.physx.get_physx_scene_query_interface()
    blocked = set()
    pieces = 0
    missing = []
    details = []
    for link in links:
        path = str(link.prim_path)
        if path not in moving or getattr(link, 'visual_only', False):
            continue
        apis = getattr(link, '_collision_apis', ())
        if apis and not any(api.GetCollisionEnabledAttr().Get() for api in apis):
            continue
        meshes = list(getattr(link, 'collision_meshes', {}).values())
        if not meshes:
            missing.append(path)
        for mesh in meshes:
            check()
            vertices = mesh.points.cpu().numpy()
            if not len(vertices):
                missing.append(str(mesh.prim_path))
                continue
            transform = mesh.scaled_transform.cpu().numpy()
            vertices = vertices @ transform[:3, :3].T + transform[:3, 3]
            if not np.isfinite(vertices).all():
                raise ValueError('Nonfinite articulated collision geometry')
            unresolved = None
            mesh_queries = []
            for basis, (center, half, axes) in collision_piece_enclosures(vertices, transform):
                check()
                quaternion = T.mat2quat(backend.torch.as_tensor(axes, dtype=backend.torch.float32)).cpu().tolist()
                errors = []
                hits = set()

                def hit(row):
                    try:
                        body = str(row.rigid_body)
                        if body not in ignored:
                            hits.add(body)
                    except Exception as exc:
                        errors.append(exc)
                    return True

                query.overlap_box(tuple(map(float, half)), tuple(map(float, center)), tuple(quaternion), hit, False)
                if errors:
                    raise RuntimeError('Articulation clearance callback failed') from errors[0]
                mesh_queries.append(dict(basis=basis, hits=sorted(hits),
                    center=center.tolist(), half_extents=half.tolist(), orientation=quaternion))
                unresolved = hits if unresolved is None else unresolved & hits
                if not unresolved:
                    break
            blocked.update((path, body) for body in unresolved)
            details.append(dict(mesh=str(mesh.prim_path), enclosures=mesh_queries))
            pieces += 1
    return dict(clear=not blocked and not missing and bool(pieces),
                overlapping_enclosures=sorted(blocked), queried_collision_pieces=pieces,
                missing_collision_geometry=missing,
                collision_piece_queries=details,
                method='per_collision_mesh_enclosure_intersection_no_physics')
