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
        self.angular_blocks = OrderedDict()
        self.grids = OrderedDict()
        self.window = None
        self._optimistic = None
        self._guidance = None
        self._guidance_components = None
        self._guidance_graph = None
        self.turns = OrderedDict()

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
        result.angular_blocks=self.angular_blocks
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
        # Cached dyadic unions reproduce the exact same integer-degree bins
        # with at most two block unions for any requested sweep.
        size = 1 << (span.bit_length()-1)
        first = self._angular_block(low, size)
        if size == span:
            result = first
        else:
            last = self._angular_block(low+span-size, size)
            result = [a | b for a,b in zip(first,last)]
        return self._put(self.kernels, key, result, 256)

    def _angular_block(self, low, size):
        import numpy as np
        from .live_gt_map import footprint_kernel
        self.check_cancelled()
        low %= 360
        key = (low,size)
        if key in self.angular_blocks:
            self.angular_blocks.move_to_end(key)
            return self.angular_blocks[key]
        if size > 1:
            first = self._angular_block(low,size//2)
            last = self._angular_block(low+size//2,size//2)
            result = [a | b for a,b in zip(first,last)]
        else:
            result = self.angular_bins.get(low)
            if result is None:
                result = []
                for _, points in self.layers:
                    radius = float(np.linalg.norm(points, axis=1).max())
                    margin = self.grid.resolution/2 + radius*(1-math.cos(math.radians(1)))
                    offsets = np.array([[-margin,-margin], [-margin,margin],
                                        [margin,-margin], [margin,margin]])
                    swept = []
                    for a in (low, low+.5, low+1):
                        yaw = math.radians(a)-self.reference_yaw
                        c, s = math.cos(yaw), math.sin(yaw)
                        rotated = points @ np.array([[c,s], [-s,c]])
                        swept.append((rotated[:,None,:]+offsets).reshape(-1,2))
                    kernel = footprint_kernel(np.concatenate(swept), self.grid.resolution)
                    half = math.ceil((radius+math.sqrt(2)*margin)/self.grid.resolution)+1
                    pad = half-kernel.shape[0]//2
                    result.append(np.pad(kernel, pad))
                self._put(self.angular_bins, low, result, 360)
        return self._put(self.angular_blocks,key,result,2048)

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
            if key in self.grids:
                return self._sweep_grid(key).segment_free(start[:2], end[:2])
            return self._translation_free(key,start,end)
        # In-place turns need only a small local occupancy window, not a new
        # full-floor erosion on every angular command or A* expansion.
        row, col = self.grid.cell(start[:2])
        if not self.grid.navigable((row, col)):
            return False
        turn_key = (row,col,key)
        cached = self.turns.get(turn_key)
        if cached is not None:
            return cached
        for (raw, _), kernel in zip(self.layers, self._kernels(key)):
            half = kernel.shape[0]//2
            if row-half < 0 or col-half < 0 or row+half >= self.grid.height or col+half >= self.grid.width:
                return self._put(self.turns,turn_key,False,65536)
            window = raw[row-half:row+half+1, col-half:col+half+1]
            if np.any((window == 0) & (kernel != 0)):
                return self._put(self.turns,turn_key,False,65536)
        return self._put(self.turns,turn_key,True,65536)

    def _translation_free(self,key,start,end):
        """Erode only the segment's cells, reading the full obstacle halo.

        Rare continuous headings need no full-floor grid. The kernel and
        supercover are identical to the full-map check, including neighboring
        cells when the segment lies on a cell edge.
        """
        import cv2
        import numpy as np
        a,b=self.grid.cell(start[:2]),self.grid.cell(end[:2])
        if not self.grid.contains(a) or not self.grid.contains(b):return False
        r0,r1=max(0,min(a[0],b[0])-1),min(self.grid.height,max(a[0],b[0])+2)
        c0,c1=max(0,min(a[1],b[1])-1),min(self.grid.width,max(a[1],b[1])+2)
        support=np.frombuffer(self.grid.free,dtype='uint8').reshape(self.grid.height,self.grid.width)
        free=support[r0:r1,c0:c1].copy()
        if self.window is not None:
            ra,rb,ca,cb=self.window
            free[(np.arange(r0,r1)<ra)|(np.arange(r0,r1)>=rb),:]=0
            free[:,(np.arange(c0,c1)<ca)|(np.arange(c0,c1)>=cb)]=0
        for (raw,_),kernel in zip(self.layers,self._kernels(key)):
            self.check_cancelled()
            half=kernel.shape[0]//2
            ra,rb=max(0,r0-half),min(self.grid.height,r1+half)
            ca,cb=max(0,c0-half),min(self.grid.width,c1+half)
            eroded=cv2.erode(raw[ra:rb,ca:cb],kernel,borderType=cv2.BORDER_CONSTANT,borderValue=0)
            free &= eroded[r0-ra:r1-ra,c0-ca:c1-ca]
        segment=GridMap(c1-c0,r1-r0,self.grid.resolution,self.grid.world((r0,c0)),free.tobytes())
        return segment.segment_free(start[:2],end[:2])

    def grid_at_heading(self, yaw):
        return self._sweep_grid(self._key(yaw, yaw))

    def optimistic_grid(self):
        """XY guide valid for arbitrary headings, using inscribed body discs.

        A union of eight oriented footprints can erase an aisle which only
        permits an intermediate heading. A disc inside a footprint at the base
        centre is occupied at every heading. Offset payload layers without such
        a disc impose no constraint here; full sweeps still check every edge.
        """
        import cv2
        import numpy as np
        self.check_cancelled()
        if self._optimistic is not None:
            return self._optimistic
        support = np.frombuffer(self.grid.free, dtype='uint8').reshape(self.grid.height,self.grid.width)
        r0,r1,c0,c1 = self.window or (0,self.grid.height,0,self.grid.width)
        free = np.zeros_like(support)
        region = free[r0:r1,c0:c1]
        region[:] = support[r0:r1,c0:c1]
        for raw, points in self.layers:
            self.check_cancelled()
            hull = cv2.convexHull(np.asarray(points,dtype='float32')).reshape(-1,2).astype(float)
            if len(hull) < 3:
                continue
            edges = np.roll(hull,-1,axis=0)-hull
            cross = edges[:,1]*hull[:,0]-edges[:,0]*hull[:,1]
            if not (np.all(cross >= 0.) or np.all(cross <= 0.)):
                continue
            lengths = np.linalg.norm(edges,axis=1)
            radius = max(0.,float((np.abs(cross)/np.maximum(lengths,1e-12)).min())-1e-6)
            half = math.floor(radius/self.grid.resolution)
            axis = np.arange(-half,half+1)*self.grid.resolution
            kernel = (axis[:,None]**2+axis[None,:]**2 <= radius**2).astype('uint8')
            a,b = max(0,r0-half),min(self.grid.height,r1+half)
            c,d = max(0,c0-half),min(self.grid.width,c1+half)
            eroded = cv2.erode(raw[a:b,c:d],kernel,borderType=cv2.BORDER_CONSTANT,borderValue=0)
            region &= eroded[r0-a:r1-a,c0-c:c1-c]
        self._optimistic = GridMap(self.grid.width,self.grid.height,self.grid.resolution,
                                   self.grid.origin,free.tobytes())
        return self._optimistic

    def guidance_grid(self):
        """A tighter sampled-orientation guide, never a reachability gate.

        Its finite costs help avoid long-payload dead ends ignored by the
        inscribed-disc relaxation. Search must fall back outside its coverage;
        continuous poses are checked against full geometry, not this raster.
        """
        import numpy as np
        self.check_cancelled()
        if self._guidance is None:
            free = np.zeros((self.grid.height,self.grid.width),dtype='uint8')
            for heading in range(8):
                self.check_cancelled()
                grid = self.grid_at_heading(heading*math.pi/4)
                free |= np.frombuffer(grid.free,dtype='uint8').reshape(free.shape)
            self._guidance = GridMap(self.grid.width,self.grid.height,self.grid.resolution,
                                     self.grid.origin,free.tobytes())
        return self._guidance

    def guidance_components(self):
        """Cheap guide connectivity, used only to avoid futile distance floods.

        Grid diagonals forbid corner cutting, so four-neighbour components
        have the same connectivity. Missing guide connectivity never rejects
        an actual continuous pose or a checked motion edge.
        """
        import cv2
        import numpy as np
        self.check_cancelled()
        if self._guidance_components is None:
            grid=self.guidance_grid()
            free=np.frombuffer(grid.free,dtype='uint8').reshape(grid.height,grid.width)
            _,self._guidance_components=cv2.connectedComponents(free,connectivity=4)
        return self._guidance_components

    def guidance_distances(self, seeds):
        """Compute the shared XY ordering field in compiled graph code.

        This sampled-heading map is guidance only. Infinite distances are not
        evidence of physical infeasibility; callers retain Euclidean fallback.
        """
        import numpy as np
        from scipy.sparse import coo_matrix, vstack
        from scipy.sparse.csgraph import dijkstra
        self.check_cancelled()
        grid=self.guidance_grid()
        if self._guidance_graph is None:
            free=np.frombuffer(grid.free,dtype='uint8').reshape(grid.height,grid.width).astype(bool)
            # This field orders continuous-pose search; it never certifies a
            # motion. Building eight edges for every 5 cm cell of a whole
            # building can consume the entire action budget before expansion.
            # Pool at 20 cm for guidance, keeping any partly free block. Narrow
            # passages remain proposals; full-resolution sweeps check every
            # actual edge and Euclidean guidance remains available elsewhere.
            stride=max(1,int(round(.20/grid.resolution)))
            height=math.ceil(grid.height/stride);width=math.ceil(grid.width/stride)
            if stride>1:
                padded=np.zeros((height*stride,width*stride),dtype=bool)
                padded[:grid.height,:grid.width]=free
                free=padded.reshape(height,stride,width,stride).any(axis=(1,3))
            flat=np.flatnonzero(free)
            indices=np.full(free.shape,-1,dtype=np.int32)
            indices.ravel()[flat]=np.arange(len(flat),dtype=np.int32)
            rows=[];cols=[];weights=[]
            for dr,dc in ((-1,0),(1,0),(0,-1),(0,1),(-1,-1),(-1,1),(1,-1),(1,1)):
                self.check_cancelled()
                r0,r1=max(0,-dr),min(height,height-dr)
                c0,c1=max(0,-dc),min(width,width-dc)
                valid=free[r0:r1,c0:c1]&free[r0+dr:r1+dr,c0+dc:c1+dc]
                if dr and dc:valid &= free[r0:r1,c0+dc:c1+dc]&free[r0+dr:r1+dr,c0:c1]
                source=indices[r0:r1,c0:c1][valid]
                rows.append(source);cols.append(indices[r0+dr:r1+dr,c0+dc:c1+dc][valid])
                weights.append(np.full(len(source),stride*grid.resolution*math.hypot(dr,dc)))
            graph=coo_matrix((np.concatenate(weights),(np.concatenate(rows),np.concatenate(cols))),
                             shape=(len(flat),len(flat)+1)).tocsr()
            self._guidance_graph=(graph,indices,flat,stride)
        graph,indices,flat,stride=self._guidance_graph
        goals={}
        for cell,cost in seeds.items():
            if not grid.contains(cell):continue
            node=int(indices[cell[0]//stride,cell[1]//stride])
            if node>=0:goals[node]=min(goals.get(node,math.inf),float(cost))
        distances=np.full(indices.shape,np.inf)
        if goals:
            virtual=coo_matrix((list(goals.values()),(np.zeros(len(goals),dtype=int),list(goals))),
                               shape=(1,len(flat)+1)).tocsr()
            self.check_cancelled()
            field=dijkstra(vstack((graph,virtual),format='csr'),directed=True,indices=len(flat))
            distances.ravel()[flat]=field[:-1]
        self.check_cancelled()
        return distances.repeat(stride,axis=0).repeat(stride,axis=1)[:grid.height,:grid.width]
