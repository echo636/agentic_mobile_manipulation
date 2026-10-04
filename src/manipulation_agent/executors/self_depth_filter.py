"""Remove own-robot depth returns before private online mapping.

Only caller-supplied WORLD visual boundary points of the robot's own links are
used. This helper never reads a scene registry or another object's geometry.
Each link is approximated by its visual convex hull, expanded by 5 mm by default.
This is an ego-body sensor filter, not a collision or traversability oracle.
Masked depth becomes NaN: it creates neither an obstacle nor a free-space ray.
Pixels behind the robot remain unobserved. Public RGB and raw depth stay intact.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math

import numpy as np

from .depth_scan import DepthFrame


def _immutable(values):
    values = np.asarray(values, dtype=np.float64)
    return np.frombuffer(values.tobytes(), dtype=np.float64).reshape(values.shape)


@dataclass(frozen=True)
class SelfHull:
    link: str
    minimum: np.ndarray = field(repr=False, compare=False)
    maximum: np.ndarray = field(repr=False, compare=False)
    planes: np.ndarray = field(repr=False, compare=False)
    tolerance_m: float


def prepare_self_hulls(link_visual_points, *, tolerance_m=.005):
    """Compile known robot-link convex hulls once per synchronized capture.

    ``link_visual_points`` maps own-link names to WORLD Nx3 visual boundary
    points. The caller must collect these after rendering on the simulator
    owner thread. No hull from another object may be passed. Returns immutable
    hulls plus a private geometry report. Degenerate links are explicitly
    skipped; their AABB is never used as a substitute body mask.
    """
    from scipy.spatial import ConvexHull, QhullError
    if isinstance(tolerance_m, bool) or not math.isfinite(tolerance_m) or tolerance_m < 0:
        raise ValueError('Self-filter tolerance must be finite and nonnegative')
    hulls, skipped = [], {}
    for name, values in link_visual_points.items():
        if values is None:
            skipped[str(name)] = 'no_visual_points'
            continue
        points = np.asarray(values, dtype=np.float64)
        if points.ndim != 2 or points.shape[1:] != (3,) or len(points) < 4:
            skipped[str(name)] = 'insufficient_3d_points'
            continue
        if not np.isfinite(points).all():
            skipped[str(name)] = 'nonfinite_visual_points'
            continue
        try:
            hull = ConvexHull(points)
        except QhullError:
            skipped[str(name)] = 'degenerate_visual_hull'
            continue
        hulls.append(SelfHull(str(name), _immutable(points.min(axis=0) - tolerance_m),
                              _immutable(points.max(axis=0) + tolerance_m),
                              _immutable(hull.equations), float(tolerance_m)))
    return tuple(hulls), {'links_supplied': len(link_visual_points), 'hulls_prepared': len(hulls),
                         'skipped_links': skipped, 'tolerance_m': float(tolerance_m),
                         'geometry_source': 'own_robot_visual_convex_hulls_only'}


def mask_self_depth(frame: DepthFrame, hulls, *, pixel_stride=4):
    """Return a new immutable frame and private masking counts.

    Only samples selected by ``pixel_stride`` are classified; use the same
    stride as ProjectionConfig. Full camera intrinsics and WORLD extrinsics
    are applied, including pitch and camera offsets. AABB checks preselect
    points; only actual convex-hull half-space tests can mask them.
    """
    if isinstance(pixel_stride, bool) or not isinstance(pixel_stride, int) or pixel_stride < 1:
        raise ValueError('pixel_stride must be a positive integer')
    rows, cols = np.meshgrid(np.arange(0, frame.depth_linear.shape[0], pixel_stride),
                            np.arange(0, frame.depth_linear.shape[1], pixel_stride), indexing='ij')
    depth = frame.depth_linear[rows, cols].ravel()
    valid = np.isfinite(depth) & (depth > 0)
    y, x = rows.ravel()[valid], cols.ravel()[valid]
    pinhole = np.column_stack((x, y, np.ones(len(x)))) @ np.linalg.inv(frame.intrinsic).T
    local = pinhole * [1., -1., -1.] * depth[valid, None]
    points = local @ frame.world_from_camera[:3, :3].T + frame.world_from_camera[:3, 3]
    masked = np.zeros(len(points), dtype=bool)
    counts = {}
    for hull in hulls:
        candidate = np.flatnonzero(~masked & np.all((points >= hull.minimum) & (points <= hull.maximum), axis=1))
        if not len(candidate):
            continue
        distance = points[candidate] @ hull.planes[:, :3].T + hull.planes[:, 3]
        inside = np.all(distance <= hull.tolerance_m + 1e-10, axis=1)
        indices = candidate[inside]
        if len(indices):
            masked[indices] = True
            counts[hull.link] = len(indices)
    filtered = frame.depth_linear.copy()
    filtered[y[masked], x[masked]] = np.nan
    output = DepthFrame(frame.view, frame.capture_id, frame.sim_step, filtered,
                        frame.intrinsic, frame.world_from_camera)
    return output, {'view': frame.view, 'sampled_pixels': int(rows.size),
                    'valid_samples': int(valid.sum()), 'self_masked_samples': int(masked.sum()),
                    'masked_by_link': counts, 'pixel_stride': pixel_stride,
                    'masked_value': 'invalid_no_ray', 'public_frame_modified': False}
