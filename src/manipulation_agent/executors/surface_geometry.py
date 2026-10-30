"""Selected-surface pose construction from actual collision geometry.

No simulator or task predicates are used here. Geometry is already expressed
in world coordinates, including the object's current orientation and scale.
"""


def single_support_height(triangles):
    """Identify a single horizontal receiving plane, never choose among shelves."""
    import numpy as np
    faces = np.asarray(triangles, dtype=float).reshape(-1, 3, 3)
    normals = np.cross(faces[:, 1]-faces[:, 0], faces[:, 2]-faces[:, 0])
    lengths = np.linalg.norm(normals, axis=1)
    upward = faces[(lengths > 1e-10) & (normals[:, 2] > .9*lengths)]
    if not len(upward) or np.ptp(upward[:, :, 2]) > .01:
        return None
    return float(np.mean(upward[:, :, 2]))


def bottom_anchor(points):
    """Return the center of the lowest collision-geometry patch.

    An elevated handle does not move this anchor toward the edge of a pan.
    This is a landing proposal, not a support/stability certificate: the
    simulator must subsequently test collision and free settling.
    """
    import numpy as np
    points = np.asarray(points, dtype=float)
    if points.ndim != 2 or points.shape[1] != 3 or not len(points) or not np.isfinite(points).all():
        raise ValueError('Placement requires finite, nonempty Nx3 collision geometry')
    minimum = points[:, 2].min()
    band = max(.002, min(.01, float(np.ptp(points[:, 2])) * .05))
    bottom = points[points[:, 2] <= minimum + band]
    anchor = (bottom.min(axis=0) + bottom.max(axis=0)) / 2
    anchor[2] = minimum
    return anchor


def bottom_support_points(points, maximum=17):
    """Probe the lower shape as well as its center, which may lie in a hole.

    Actual lower vertices are spread over the patch by farthest-point sampling.
    A support hit is still only one part of physical settling verification.
    """
    import numpy as np
    points = np.asarray(points, dtype=float)
    anchor = bottom_anchor(points)
    band = max(.002, min(.01, float(np.ptp(points[:, 2])) * .05))
    bottom = np.unique(points[points[:, 2] <= anchor[2] + band], axis=0)
    probes = [anchor]
    distances = np.sum((bottom[:, :2]-anchor[:2])**2, axis=1)
    while len(probes) < maximum and len(bottom):
        index = int(np.argmax(distances))
        if distances[index] < 1e-10:
            break
        probe = bottom[index]
        probes.append(probe)
        distances = np.minimum(distances, np.sum((bottom[:, :2]-probe[:2])**2, axis=1))
    return np.asarray(probes)


def rotated_surface_geometry(points_world, origin, yaw_degrees=None):
    """Rotate the current shape about its root by a relative world-Z yaw.

    Returns root-relative rotated vertices and their bottom anchor. A null yaw
    preserves the held orientation; this never expands the shape to a square.
    """
    import numpy as np
    points = np.asarray(points_world, dtype=float)
    origin = np.asarray(origin, dtype=float)
    if origin.shape != (3,) or not np.isfinite(origin).all():
        raise ValueError('Placement requires a finite three-dimensional object origin')
    # Validate before subtraction so malformed shapes have a useful error.
    bottom_anchor(points)
    relative = points - origin
    if yaw_degrees is not None:
        angle = np.deg2rad(float(yaw_degrees))
        if not np.isfinite(angle):
            raise ValueError('Placement yaw must be finite')
        cosine, sine = np.cos(angle), np.sin(angle)
        rotation = np.array([[cosine, -sine, 0.], [sine, cosine, 0.], [0., 0., 1.]])
        relative = relative @ rotation.T
    return relative, bottom_anchor(relative)


def native_resting_orientation(current, orientations):
    """Use the nearest authored resting tilt while preserving world heading.

    DatasetObject.sample_orientation uses identity for an empty orientation
    table. Reuse those native poses without its random yaw. This constructs a
    proposal only; collision, support and payload still need free settling.
    """
    import numpy as np
    from scipy.spatial.transform import Rotation
    current = Rotation.from_quat(current)
    poses = [entry['rotation'] for entry in orientations.values()
             if entry['prob'] > 0] if orientations else [[0., 0., 0., 1.]]
    candidates = []
    for pose in poses:
        stable = Rotation.from_quat(pose)
        old_axes, new_axes = current.as_matrix(), stable.as_matrix()
        # Use a shared horizontal axis even if the object's X axis is vertical.
        axis = int(np.argmax(np.minimum(np.linalg.norm(old_axes[:2], axis=0),
                                        np.linalg.norm(new_axes[:2], axis=0))))
        yaw = (np.arctan2(old_axes[1, axis], old_axes[0, axis]) -
               np.arctan2(new_axes[1, axis], new_axes[0, axis]))
        aligned = Rotation.from_rotvec([0., 0., yaw]) * stable
        candidates.append(aligned)
    selected = min(candidates, key=lambda pose: (pose * current.inv()).magnitude())
    return selected.as_quat(), (selected * current.inv()).as_matrix()


def overlapping_surface_probes(lower_faces, support_faces, height, maximum=32):
    """Find interior witnesses of overlapping physical faces near a support plane.

    Vertex rays miss contact between a broad bottom face and a narrow box rim.
    Intersect projected triangles instead; the simulator must still ray-check
    each proposed witness against the actual colliders and verify free settling.
    """
    import numpy as np
    lower_faces = np.asarray(lower_faces, dtype=float).reshape(-1, 3, 3)
    support_faces = np.asarray(support_faces, dtype=float).reshape(-1, 3, 3)

    def select(faces, low, high):
        normals = np.cross(faces[:, 1]-faces[:, 0], faces[:, 2]-faces[:, 0])
        length = np.linalg.norm(normals, axis=1)
        keep = (np.isfinite(faces).all(axis=(1, 2)) & (length > 1e-12)
                & (np.abs(normals[:, 2]) >= .9*length)
                & (faces[:, :, 2].max(axis=1) >= low)
                & (faces[:, :, 2].min(axis=1) <= high))
        return faces[keep], normals[keep]

    lower_faces, lower_normals = select(lower_faces, height-.06, height+.065)
    support_faces, support_normals = select(support_faces, height-.05, height+.05)
    if not len(lower_faces) or not len(support_faces):
        return np.empty((0, 3))

    def cross(a, b):
        return a[0]*b[1]-a[1]*b[0]

    def intersect(subject, clip):
        sign = 1. if cross(clip[1]-clip[0], clip[2]-clip[0]) >= 0 else -1.
        polygon = list(subject)
        for a, b in zip(clip, np.roll(clip, -1, axis=0)):
            if not polygon:
                break
            output = []
            previous = polygon[-1]
            before = sign*cross(b-a, previous-a)
            for current in polygon:
                after = sign*cross(b-a, current-a)
                if (before >= 0) != (after >= 0):
                    output.append(previous+(current-previous)*(before/(before-after)))
                if after >= 0:
                    output.append(current)
                previous, before = current, after
            polygon = output
        return np.asarray(polygon)

    support_lo = support_faces[:, :, :2].min(axis=1)
    support_hi = support_faces[:, :, :2].max(axis=1)
    proposals = []
    for face, normal in zip(lower_faces, lower_normals):
        lo, hi = face[:, :2].min(axis=0), face[:, :2].max(axis=0)
        nearby = np.flatnonzero(((support_hi >= lo) & (support_lo <= hi)).all(axis=1))
        for index in nearby:
            support, support_normal = support_faces[index], support_normals[index]
            polygon = intersect(face[:, :2], support[:, :2])
            if len(polygon) < 3:
                continue
            area = abs(sum(cross(a, b) for a, b in zip(polygon, np.roll(polygon, -1, axis=0))))/2
            if area <= 1e-12:
                continue
            xy = polygon.mean(axis=0)
            z = face[0, 2]-np.dot(normal[:2], xy-face[0, :2])/normal[2]
            support_z = support[0, 2]-np.dot(support_normal[:2], xy-support[0, :2])/support_normal[2]
            if -.01 <= z-support_z <= .015 and abs(support_z-height) <= .05:
                proposals.append((area, np.array([*xy, z])))
    points, seen = [], set()
    for _, point in sorted(proposals, key=lambda item: -item[0]):
        key = tuple(np.round(point, 6))
        if key not in seen:
            seen.add(key)
            points.append(point)
            if len(points) >= maximum:
                break
    return np.asarray(points).reshape(-1, 3)
