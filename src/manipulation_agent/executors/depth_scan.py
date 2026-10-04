"""Private, CPU-only depth projection for an online navigation mapper.

This is our OmniGibson adapter, not an upstream Cartographer mapper. It reads no
scene, object registry, navmesh or traversability map, and does not integrate a
map or move a robot. Capture depth, K and WORLD camera transforms together on the
simulator owner thread, then pass these immutable copies to a CPU consumer.

``depth_linear`` is axial distance along camera -Z. Image right is camera +X;
image down is camera -Y. Full extrinsics preserve pitch and each camera's offset.
Every view retains its own ray origin: merging these into one zero-origin scan
would fabricate free rays through areas the offset camera did not observe.

Returns are surface hits in the configured height band. Misses are measured
rays with a clear segment in that band and no in-range obstacle endpoint. Floor
support is separate evidence. Clear segments describe one observed 3D ray, NOT
proof that an entire 2D cell or the robot's full height is free. A mapper must
preserve this distinction. Invalid/no-return depth supplies no free evidence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math

import numpy as np


VIEWS = frozenset(("front", "back", "left", "right"))


def _readonly_array(value, shape=None):
    array = np.asarray(value, dtype=np.float64)
    if shape is not None and array.shape != shape:
        raise ValueError(f"Expected shape {shape}, got {array.shape}")
    # An immutable bytes backing also prevents setflags(write=True).
    return np.frombuffer(array.tobytes(), dtype=np.float64).reshape(array.shape)


@dataclass(frozen=True)
class DepthFrame:
    view: str
    capture_id: str
    sim_step: int
    depth_linear: np.ndarray = field(repr=False, compare=False)
    intrinsic: np.ndarray = field(repr=False, compare=False)
    world_from_camera: np.ndarray = field(repr=False, compare=False)

    def __post_init__(self):
        if self.view not in VIEWS:
            raise ValueError("Expected front/back/left/right view")
        if not isinstance(self.capture_id, str) or not self.capture_id:
            raise ValueError("A capture identity is required")
        if isinstance(self.sim_step, bool) or not isinstance(self.sim_step, int) or self.sim_step < 0:
            raise ValueError("sim_step must be a nonnegative integer")
        depth = _readonly_array(self.depth_linear)
        if depth.ndim != 2 or not all(depth.shape):
            raise ValueError("depth_linear must be a nonempty H x W array")
        intrinsic = _readonly_array(self.intrinsic, (3, 3))
        if (not np.isfinite(intrinsic).all() or intrinsic[0, 0] <= 0 or intrinsic[1, 1] <= 0
                or not np.allclose(intrinsic[2], [0, 0, 1], atol=1e-8, rtol=0)
                or abs(intrinsic[1, 0]) > 1e-8):
            raise ValueError("Expected finite calibrated pinhole intrinsics")
        transform = _readonly_array(self.world_from_camera, (4, 4))
        rotation = transform[:3, :3]
        if (not np.isfinite(transform).all()
                or not np.allclose(transform[3], [0, 0, 0, 1], atol=1e-8, rtol=0)
                or not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-5, rtol=0)
                or not math.isclose(float(np.linalg.det(rotation)), 1.0, abs_tol=1e-5)):
            raise ValueError("world_from_camera must be a finite rigid transform")
        object.__setattr__(self, "depth_linear", depth)
        object.__setattr__(self, "intrinsic", intrinsic)
        object.__setattr__(self, "world_from_camera", transform)


@dataclass(frozen=True)
class ProjectionConfig:
    # Caller supplies a local floor-height reference, not a precomputed map.
    floor_height_m: float
    obstacle_min_height_m: float = 0.15
    obstacle_max_height_m: float = 1.25
    floor_tolerance_m: float = 0.04
    min_depth_m: float = 0.05
    max_depth_m: float = 30.0
    max_planar_range_m: float = 8.0
    hit_clearance_m: float = 0.01
    pixel_stride: int = 4
    angular_bins: int = 720

    def __post_init__(self):
        for name in ("floor_height_m", "obstacle_min_height_m", "obstacle_max_height_m",
                     "floor_tolerance_m", "min_depth_m", "max_depth_m",
                     "max_planar_range_m", "hit_clearance_m"):
            value = getattr(self, name)
            if isinstance(value, bool) or not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        if not (0 <= self.floor_tolerance_m < self.obstacle_min_height_m < self.obstacle_max_height_m
                and 0 < self.min_depth_m < self.max_depth_m and self.max_planar_range_m > 0
                and 0 <= self.hit_clearance_m < self.max_planar_range_m):
            raise ValueError("Invalid depth, height band or planar range")
        for name in ("pixel_stride", "angular_bins"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True)
class RayEvidence:
    """One selected measured ray; coordinates are WORLD XYZ, meters.

    ``clear_segment_world`` is clipped to both the obstacle height band and
    range, and stops before a hit. Its start can differ from camera_origin_world
    when the camera is above the height band. Empty means no free evidence.
    ``endpoint_kind`` describes the actual measured surface, even for a miss.
    """
    pixel: tuple[int, int]
    beam_index: int
    camera_origin_world: tuple[float, float, float]
    measured_surface_world: tuple[float, float, float]
    endpoint_kind: str
    clear_segment_world: tuple[tuple[float, float, float], ...]
    range_censored: bool


@dataclass(frozen=True)
class FloorSupport:
    pixel: tuple[int, int]
    point_world: tuple[float, float, float]


@dataclass(frozen=True)
class PlanarViewScan:
    view: str
    capture_id: str
    sim_step: int
    camera_origin_world: tuple[float, float, float]
    angular_bins: int
    max_planar_range_m: float
    returns: tuple[RayEvidence, ...]
    misses: tuple[RayEvidence, ...]
    floor_support: tuple[FloorSupport, ...]
    sampled_pixels: int
    valid_depth_pixels: int


def _xyz(point):
    return tuple(float(x) for x in point)


def _project_view(frame: DepthFrame, config: ProjectionConfig) -> PlanarViewScan:
    rows, cols = np.meshgrid(np.arange(0, frame.depth_linear.shape[0], config.pixel_stride),
                            np.arange(0, frame.depth_linear.shape[1], config.pixel_stride), indexing="ij")
    pixels = np.column_stack((cols.ravel(), rows.ravel()))
    depth = frame.depth_linear[rows, cols].ravel()
    valid = np.isfinite(depth) & (depth >= config.min_depth_m) & (depth <= config.max_depth_m)
    pixels, depth = pixels[valid], depth[valid]
    origin = frame.world_from_camera[:3, 3]
    pinhole = np.column_stack((pixels, np.ones(len(pixels)))) @ np.linalg.inv(frame.intrinsic).T
    local = pinhole * np.array([1.0, -1.0, -1.0]) * depth[:, None]
    points = local @ frame.world_from_camera[:3, :3].T + origin
    delta = points - origin
    ranges = np.linalg.norm(delta[:, :2], axis=1)
    angles = np.arctan2(delta[:, 1], delta[:, 0])
    bins = (np.floor((angles + math.pi) * config.angular_bins / (2 * math.pi)).astype(int)
            % config.angular_bins)
    returns, misses, floors = {}, {}, {}
    lo = config.floor_height_m + config.obstacle_min_height_m
    hi = config.floor_height_m + config.obstacle_max_height_m
    for pixel, point, vector, distance, beam in zip(pixels, points, delta, ranges, bins):
        height = point[2] - config.floor_height_m
        kind = ("floor" if abs(height) <= config.floor_tolerance_m else
                "obstacle" if config.obstacle_min_height_m <= height <= config.obstacle_max_height_m else
                "below_band" if height < config.obstacle_min_height_m else "above_band")
        if kind == "floor" and distance <= config.max_planar_range_m:
            if beam not in floors or distance < floors[beam][0]:
                floors[beam] = (distance, FloorSupport(tuple(map(int, pixel)), _xyz(point)))
        if distance < 1e-9:
            continue  # A vertical ray has no planar free segment.
        within_range = distance <= config.max_planar_range_m
        hit = kind == "obstacle" and within_range
        maximum_t = min(1.0, config.max_planar_range_m / distance)
        # Surface points themselves never count as empty space.
        maximum_t = min(maximum_t, max(0.0, 1.0 - config.hit_clearance_m / distance))
        if abs(vector[2]) < 1e-10:
            t0, t1 = (0.0, maximum_t) if lo <= origin[2] <= hi else (0.0, 0.0)
        else:
            band = sorted(((lo - origin[2]) / vector[2], (hi - origin[2]) / vector[2]))
            t0, t1 = max(0.0, band[0]), min(maximum_t, band[1])
        clear = (() if t1 <= t0 or (t1 - t0) * distance < 1e-6 else
                 (_xyz(origin + t0 * vector), _xyz(origin + t1 * vector)))
        evidence = RayEvidence(tuple(map(int, pixel)), int(beam), _xyz(origin), _xyz(point), kind,
                               clear, not within_range)
        if hit:
            if beam not in returns or distance < returns[beam][0]:
                returns[beam] = (distance, evidence)
        elif clear:
            length = (t1 - t0) * distance
            if beam not in misses or length > misses[beam][0]:
                misses[beam] = (length, evidence)
    # Never let another height sample clear behind a detected obstacle in the
    # same angular bin. Rays from different camera origins remain separate.
    return PlanarViewScan(frame.view, frame.capture_id, frame.sim_step, _xyz(origin), config.angular_bins,
                          config.max_planar_range_m,
                          tuple(returns[k][1] for k in sorted(returns)),
                          tuple(misses[k][1] for k in sorted(misses) if k not in returns),
                          tuple(floors[k][1] for k in sorted(floors)), int(rows.size), int(valid.sum()))


def project_four_depth_frames(frames, config: ProjectionConfig) -> tuple[PlanarViewScan, ...]:
    """Project one immutable, synchronized capture; returns front/back/left/right.

    Each view has its own 720-bin (by default) sparse evidence set, with bins
    spanning WORLD azimuth [-pi, pi). This is deliberately NOT one fused,
    zero-origin 720-range laser scan. No map updates or simulator calls occur.
    """
    by_view = {}
    for frame in frames:
        if not isinstance(frame, DepthFrame) or frame.view in by_view:
            raise ValueError("Expected one DepthFrame for each direction")
        by_view[frame.view] = frame
    if set(by_view) != VIEWS or len({(f.capture_id, f.sim_step) for f in by_view.values()}) != 1:
        raise ValueError("Expected four views from one synchronized capture")
    return tuple(_project_view(by_view[view], config) for view in ("front", "back", "left", "right"))


def to_mapper_scans(scans, pose) -> tuple[dict, ...]:
    """Convert evidence to explicit 2D occupancy-projection packets.

    ``pose`` is the synchronized robot WORLD ``(x, y, yaw_radians)``. Packets
    contain a separate, actual camera ``origin`` and ``returns``/``misses`` in
    robot planar coordinates (XYZ with Z=0). A measured floor endpoint is a
    miss, not an obstacle hit; a measured obstacle beyond the configured range
    is a miss clipped to that range. Invalid depth creates neither.

    This intentionally selects the standard 2D projection assumption: measured
    free rays project from actual camera XY to their measured/censored endpoint.
    They do NOT certify that the full robot-height column is empty. In contrast,
    RayEvidence retains the exact 3D height-clipped clear segment for inspection.
    A mapper must preserve per-camera origins and must not turn misses into hits.
    No arbitrary disk around the robot or unseen region is cleared here.
    """
    pose = np.asarray(pose, dtype=np.float64)
    if pose.shape != (3,) or not np.isfinite(pose).all():
        raise ValueError("pose must be finite WORLD (x, y, yaw_radians)")
    scans = tuple(scans)
    if (len(scans) != 4 or any(not isinstance(s, PlanarViewScan) for s in scans)
            or {s.view for s in scans} != VIEWS
            or len({(s.capture_id, s.sim_step) for s in scans}) != 1):
        raise ValueError("Expected four synchronized PlanarViewScan values")
    c, s = math.cos(pose[2]), math.sin(pose[2])
    world_to_base = np.array([[c, s], [-s, c]])

    def planar(point):
        xy = world_to_base @ (np.asarray(point[:2]) - pose[:2])
        return [float(xy[0]), float(xy[1]), 0.0]

    packets = []
    for scan in scans:
        miss_points = []
        origin = np.asarray(scan.camera_origin_world)
        for ray in scan.misses:
            if ray.endpoint_kind != "floor" and not (ray.endpoint_kind == "obstacle" and ray.range_censored):
                continue  # An out-of-band surface is not floor-support evidence.
            point = np.asarray(ray.measured_surface_world)
            distance = np.linalg.norm((point - origin)[:2])
            point = origin + (point - origin) * min(1.0, scan.max_planar_range_m / distance)
            miss_points.append(planar(point))
        packets.append({"view": scan.view, "origin": planar(scan.camera_origin_world),
                        "returns": [planar(ray.measured_surface_world) for ray in scan.returns],
                        "misses": miss_points})
    return tuple(packets)
