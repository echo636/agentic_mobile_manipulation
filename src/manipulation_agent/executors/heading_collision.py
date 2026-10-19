"""Orientation-aware collision checks on loaded height-layer geometry.

No simulator imports or state mutation. The footprints are world offsets at
reference_yaw; all other orientations are rotations of those same bodies.
"""
from collections import OrderedDict
import math

from .gt_navigation import GridMap


class HeadingCollisionChecker:
    def __init__(self, grid, collision_layers, reference_yaw, support, *,
                 check_cancelled=lambda: None):
        import numpy as np
        self.grid = GridMap(grid.width, grid.height, grid.resolution, grid.origin,
                            np.asarray(support, dtype='uint8').tobytes())
        self.layers = [(np.asarray(raw, dtype='uint8'), np.asarray(points, dtype=float))
                       for raw, points in collision_layers]
        self.reference_yaw = reference_yaw
        self.check_cancelled = check_cancelled
        self.kernels = OrderedDict()
        self.grids = OrderedDict()

    @staticmethod
    def _key(a, b):
        # Outward quantization includes feedback yaw residuals, with half a
        # degree reserve. Key reuse bounds CPU cost during straight travel.
        delta = math.atan2(math.sin(b-a), math.cos(b-a))
        start, end = math.degrees(a), math.degrees(a+delta)
        low, high = math.floor(min(start, end)-.5), math.ceil(max(start, end)+.5)
        return low%360, high-low

    @staticmethod
    def _put(cache, key, value, limit):
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > limit:
            cache.popitem(last=False)
        return value

    def _kernels(self, key):
        import numpy as np
        from .live_gt_map import footprint_kernel
        if key in self.kernels:
            self.kernels.move_to_end(key)
            return self.kernels[key]
        self.check_cancelled()
        low, span = key
        angles = np.linspace(low, low+span, max(2, math.ceil(span/2)+1))
        result = []
        # Cover subcell placement and the arc between angular samples.
        for _, points in self.layers:
            radius = float(np.linalg.norm(points, axis=1).max())
            margin = self.grid.resolution/2 + radius*(1-math.cos(math.radians(1)))
            offsets = np.array([[-margin,-margin], [-margin,margin],
                                [margin,-margin], [margin,margin]])
            swept = []
            for degrees in angles:
                yaw = math.radians(degrees)-self.reference_yaw
                c, s = math.cos(yaw), math.sin(yaw)
                rotated = points @ np.array([[c,s], [-s,c]])
                swept.append((rotated[:,None,:]+offsets).reshape(-1,2))
            result.append(footprint_kernel(np.concatenate(swept), self.grid.resolution))
        return self._put(self.kernels, key, result, 256)

    def _sweep_grid(self, key):
        import cv2
        import numpy as np
        if key in self.grids:
            self.grids.move_to_end(key)
            return self.grids[key]
        self.check_cancelled()
        free = np.frombuffer(self.grid.free, dtype='uint8').reshape(
            self.grid.height, self.grid.width).copy()
        for (raw, _), kernel in zip(self.layers, self._kernels(key)):
            free &= cv2.erode(raw, kernel, borderType=cv2.BORDER_CONSTANT, borderValue=0)
        result = GridMap(self.grid.width, self.grid.height, self.grid.resolution,
                         self.grid.origin, free.tobytes())
        return self._put(self.grids, key, result, 16)

    def motion_free(self, start, end):
        """Conservative swept-volume check, including tiny pose corrections."""
        import numpy as np
        self.check_cancelled()
        if not all(math.isfinite(v) for v in (*start, *end)):
            return False
        key = self._key(start[2], end[2])
        if math.dist(start[:2], end[:2]) > 1e-10:
            return self._sweep_grid(key).segment_free(start[:2], end[:2])
        # In-place turns need only a small local occupancy window, not a new
        # full-floor erosion on every angular command or A* expansion.
        row, col = self.grid.cell(start[:2])
        if not self.grid.navigable((row, col)):
            return False
        for (raw, _), kernel in zip(self.layers, self._kernels(key)):
            half = kernel.shape[0]//2
            if row-half < 0 or col-half < 0 or row+half >= self.grid.height or col+half >= self.grid.width:
                return False
            window = raw[row-half:row+half+1, col-half:col+half+1]
            if np.any((window == 0) & (kernel != 0)):
                return False
        return True

    def grid_at_heading(self, yaw):
        return self._sweep_grid(self._key(yaw, yaw))

    def optimistic_grid(self):
        """XY relaxation of the eight-heading search, including obstacles."""
        import numpy as np
        free = np.zeros((self.grid.height, self.grid.width), dtype='uint8')
        for heading in range(8):
            self.check_cancelled()
            g = self.grid_at_heading(heading*math.pi/4)
            free |= np.frombuffer(g.free, dtype='uint8').reshape(free.shape)
        return GridMap(self.grid.width, self.grid.height, self.grid.resolution,
                       self.grid.origin, free.tobytes())
