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
        self.angular_bins = OrderedDict()
        self.grids = OrderedDict()
        self.window = None

    def local_window(self, origin, radius):
        """Compute heading grids only near a bounded search's possible centres.

        Retain the global raster coordinates and all raw obstacle geometry.
        Erosion also reads the full footprint halo OUTSIDE the centre window.
        Other users of this checker keep their full-floor grid caches.
        """
        import numpy as np
        support=np.frombuffer(self.grid.free,dtype='uint8').reshape(self.grid.height,self.grid.width)
        result=HeadingCollisionChecker(self.grid,self.layers,self.reference_yaw,support,
                                       check_cancelled=lambda:self.check_cancelled())
        row,col=self.grid.cell(origin[:2])
        cells=math.ceil(radius/self.grid.resolution)+2
        result.window=(max(0,row-cells),min(self.grid.height,row+cells+1),
                       max(0,col-cells),min(self.grid.width,col+cells+1))
        # Footprint kernels are independent of the spatial window.
        result.kernels=self.kernels;result.angular_bins=self.angular_bins
        return result

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
        # Compose every sweep from the SAME integer-degree bins. Rasterizing
        # one convex hull for a long turn and another for a small command is
        # not monotone: OpenCV edge rounding can add a cell to the small hull
        # that is absent from the large one (archived trash episode, step 10).
        # Union of canonical bins guarantees every contained sub-sweep is a
        # subset, without weakening collision checking at execution time.
        result = None
        for degrees in range(low, low+span):
            self.check_cancelled()
            angle_key = degrees % 360
            bins = self.angular_bins.get(angle_key)
            if bins is None:
                bins = []
                for _, points in self.layers:
                    radius = float(np.linalg.norm(points, axis=1).max())
                    margin = self.grid.resolution/2 + radius*(1-math.cos(math.radians(1)))
                    offsets = np.array([[-margin,-margin], [-margin,margin],
                                        [margin,-margin], [margin,margin]])
                    swept = []
                    for a in (angle_key, angle_key+.5, angle_key+1):
                        yaw = math.radians(a)-self.reference_yaw
                        c, s = math.cos(yaw), math.sin(yaw)
                        rotated = points @ np.array([[c,s], [-s,c]])
                        swept.append((rotated[:,None,:]+offsets).reshape(-1,2))
                    kernel = footprint_kernel(np.concatenate(swept), self.grid.resolution)
                    half = math.ceil((radius+math.sqrt(2)*margin)/self.grid.resolution)+1
                    pad = half-kernel.shape[0]//2
                    bins.append(np.pad(kernel, pad))
                self._put(self.angular_bins, angle_key, bins, 360)
            if result is None:
                result = [k.copy() for k in bins]
            else:
                for total, k in zip(result, bins):
                    total |= k
        return self._put(self.kernels, key, result, 256)

    def _sweep_grid(self, key):
        import cv2
        import numpy as np
        if key in self.grids:
            self.grids.move_to_end(key)
            return self.grids[key]
        self.check_cancelled()
        support = np.frombuffer(self.grid.free, dtype='uint8').reshape(self.grid.height,self.grid.width)
        r0,r1,c0,c1=self.window or (0,self.grid.height,0,self.grid.width)
        free=np.zeros_like(support)
        region=free[r0:r1,c0:c1];region[:]=support[r0:r1,c0:c1]
        for (raw, _), kernel in zip(self.layers, self._kernels(key)):
            self.check_cancelled()
            half=kernel.shape[0]//2
            a,b=max(0,r0-half),min(self.grid.height,r1+half)
            c,d=max(0,c0-half),min(self.grid.width,c1+half)
            eroded=cv2.erode(raw[a:b,c:d],kernel,borderType=cv2.BORDER_CONSTANT,borderValue=0)
            region &= eroded[r0-a:r1-a,c0-c:c1-c]
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
