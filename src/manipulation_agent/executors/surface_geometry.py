"""Selected-surface pose construction from actual collision geometry.

No simulator or task predicates are used here. Geometry is already expressed
in world coordinates, including the object's current orientation and scale.
"""


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
