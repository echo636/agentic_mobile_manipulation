"""Conservative door/lid clearance without advancing the live physics world."""


def moving_link_clearance(backend, links, moving, ignored, check):
    """Certify an endpoint using live scene queries at current collider poses.

    Each oriented box encloses one collision mesh, not the whole object. A hit
    means this proposal is uncertified, not that the real meshes necessarily
    intersect. This deliberately avoids stepping a hypothetical interpenetrating
    articulation: restoring body state cannot repair a poisoned CUDA context.
    """
    import numpy as np
    import omnigibson.lazy as lazy
    from omnigibson.utils import transform_utils as T
    from .placement_overlap import oriented_piece_box

    query = lazy.omni.physx.get_physx_scene_query_interface()
    blocked = set()
    pieces = 0
    missing = []
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
            box = oriented_piece_box(vertices)
            if box is None:
                missing.append(str(mesh.prim_path))
                continue
            center, half, axes = box
            quaternion = T.mat2quat(backend.torch.as_tensor(axes, dtype=backend.torch.float32)).cpu().tolist()
            errors = []

            def hit(row):
                try:
                    body = str(row.rigid_body)
                    if body not in ignored:
                        blocked.add((path, body))
                except Exception as exc:
                    errors.append(exc)
                return True

            query.overlap_box(tuple(map(float, half)), tuple(map(float, center)), tuple(quaternion), hit, False)
            if errors:
                raise RuntimeError('Articulation clearance callback failed') from errors[0]
            pieces += 1
    return dict(clear=not blocked and not missing and bool(pieces),
                overlapping_enclosures=sorted(blocked), queried_collision_pieces=pieces,
                missing_collision_geometry=missing,
                method='per_collision_mesh_oriented_enclosure_no_physics')
