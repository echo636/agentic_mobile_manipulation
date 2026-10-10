"""Private current GT collision walkability on the simulator's floor support map.

PhysX overlap columns rasterize actual loaded collision geometry. Precomputed
furniture/closed-door silhouettes are not authoritative for a changed scene.
Object bounds only restrict dirty query regions; they never define occupancy.
"""
from pathlib import Path
import hashlib
import math
import time


def footprint_kernel(points, resolution):
    import cv2
    import numpy as np
    points=np.asarray(points,dtype=float)
    n=math.ceil(float(np.abs(points).max())/resolution)+1
    kernel=np.zeros((2*n+1,2*n+1),dtype='uint8')
    cv2.fillConvexPoly(kernel,cv2.convexHull(np.round((points/resolution+n)*256).astype('int32')),1,shift=8)
    return kernel


def build_navigation_grid(backend, floor):
    import cv2
    import numpy as np
    import omnigibson.lazy as lazy
    from .gt_navigation import GridMap
    scene=backend.env.scene;trav=scene.trav_map;resolution=float(trav.map_resolution)
    floor_z=float(trav.floor_heights[floor]);base=backend.robot.get_position_orientation()[0].cpu().numpy()
    # The base is wider than the torso. A single base-width column falsely
    # blocks motion beside desks/chair backs. Match occupancy by height band.
    height=float(backend.robot.aabb[1][2])-floor_z
    edges=[.03,.35,.8,1.25,max(1.26,height+.02)]
    bodies=[]
    for link in backend.robot.links.values():
        if link.is_meta_link:continue
        points=link.collision_boundary_points_world
        if points is not None:bodies.append(points.cpu().numpy())
    layers=[]
    for lo,hi in zip(edges,edges[1:]):
        chunks=[p[:,:2]-base[:2] for p in bodies if p[:,2].max()>=floor_z+lo and p[:,2].min()<=floor_z+hi]
        if not chunks:continue
        hull=cv2.convexHull(np.concatenate(chunks).astype('float32')).reshape(-1,2)
        layers.append((lo,hi,hull))
    if not layers:raise ValueError('Robot collision geometry unavailable')
    chassis=layers[0][2];kernel=footprint_kernel(chassis,resolution)
    source=Path(scene.scene_dir)/'layout'/f'floor_trav_no_obj_{floor}.png'
    cache=getattr(backend,'_live_gt_collision_cache',None)
    if cache is None:cache=backend._live_gt_collision_cache={}
    if floor not in cache:
        if not source.is_file():raise ValueError('GT floor support map unavailable: '+str(source))
        raw=cv2.imread(str(source),cv2.IMREAD_GRAYSCALE);h,w=trav.floor_map[floor].shape
        raw=(cv2.resize(raw,(w,h))==255).astype('uint8')
        cache[floor]={'support':raw,'free':np.repeat(raw[None],len(layers),axis=0),'objects':{},'height':None,
                      'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest()}
    cached=cache[floor];support=cached['support'];free=cached['free'];h,w=support.shape
    origin=np.array([-w*resolution/2,-h*resolution/2])
    held=backend._get_held();ignored_objects={backend.robot,held,*[o for o,_ in backend._carry_contents]}
    ignored_links={link.prim_path for obj in ignored_objects if obj is not None for link in obj.links.values()}
    # Navigation must not collide with semantic helper/fill volumes which have
    # no physical surface. Actual ordinary collision shapes remain included.
    ignored_links.update(link.prim_path for obj in scene.objects for link in obj.links.values() if link.is_meta_link)
    current={}
    for obj in scene.objects:
        if obj in ignored_objects:continue
        lo,hi=obj.aabb
        lo,hi=lo.cpu().numpy(),hi.cpu().numpy()
        if hi[2]<floor_z+.03 or lo[2]>floor_z+edges[-1]:continue
        position,quat=obj.get_position_orientation()
        joints=obj.get_joint_positions() if obj.n_joints else ()
        signature=tuple(round(float(v),3) for tensor in (position,quat,joints) for v in tensor)
        current[obj.name]={'signature':signature,'lo':lo[:2],'hi':hi[:2]}
    dirty=support.astype(bool) if cached['height'] is None or abs(cached['height']-height)>.01 else np.zeros_like(support,dtype=bool)
    previous=cached['objects']
    if not dirty.any():
        for name in set(current)|set(previous):
            old,new=previous.get(name),current.get(name)
            if old is not None and new is not None and old['signature']==new['signature']:continue
            for record in (old,new):
                if record is None:continue
                lo=np.floor((record['lo']-origin)/resolution).astype(int)-1
                hi=np.ceil((record['hi']-origin)/resolution).astype(int)+2
                dirty[max(0,lo[1]):min(h,hi[1]),max(0,lo[0]):min(w,hi[0])]=True
        dirty &= support.astype(bool)
    query=lazy.omni.physx.get_physx_scene_query_interface()
    started=time.monotonic();count=0
    for row,col in np.argwhere(dirty):
        if count%2048==0:backend.deadline.check()
        xy=origin+np.array([col,row])*resolution
        for layer_index,(low,high,_) in enumerate(layers):
            blocked=False;query_error=None
            def hit_callback(hit):
                nonlocal blocked,query_error
                try:
                    if str(hit.rigid_body) not in ignored_links:
                        blocked=True
                        return False
                    return True
                except Exception as exc:
                    query_error=exc
                    return False
            query.overlap_box((resolution/2,resolution/2,(high-low)/2),
                              (float(xy[0]),float(xy[1]),floor_z+(high+low)/2),
                              (0.,0.,0.,1.),hit_callback,False)
            if query_error is not None:raise RuntimeError('PhysX overlap callback failed') from query_error
            free[layer_index,row,col]=0 if blocked else 1
        count+=1
    cached.update(objects=current,height=height)
    navigable=support.copy();overlap=np.zeros_like(support,dtype='float32')
    for raw,(_,_,footprint) in zip(free,layers):
        k=footprint_kernel(footprint,resolution)
        navigable &= cv2.erode(raw,k,borderType=cv2.BORDER_CONSTANT,borderValue=0)
        overlap+=cv2.filter2D((1-raw).astype('float32'),-1,k.astype('float32'),borderType=cv2.BORDER_CONSTANT)
    grid=GridMap(w,h,resolution,tuple(origin),navigable.tobytes())
    combined=np.all(free,axis=0).astype('uint8')
    backend._navigation_clearance=(combined.tobytes(),overlap.ravel(),max(float(np.linalg.norm(p,axis=1).max()) for _,_,p in layers)+resolution)
    backend._navigation_layers=[(raw.copy(),footprint.copy()) for raw,(_,_,footprint) in zip(free,layers)]
    return grid,{'source':str(source),'source_sha256':cached['source_sha256'],
        'chassis_footprint_world_offsets':chassis.tolist(),'kernel_shape':list(kernel.shape),
        'footprint':'current_yaw_height_matched_collision_hulls',
        'collision_layers':[{'z_min':lo,'z_max':hi,'footprint':p.tolist()} for lo,hi,p in layers],
        'query_height_m':height,'updated_cells':count,'query_seconds':time.monotonic()-started,
        'dynamic_collision_check':'live_PhysX_height_matched_overlaps_incrementally_updated',
        'collision_substrate':'GT_floor_support_and_current_loaded_collision_geometry',
        'held_object_footprint_included':False}


def feasible_heading_grids(grid, raw_free, footprint, current, yaw, *, check_cancelled=None, collision_layers=None):
    """Alternative fixed headings with a collision-checked in-place turn.

    The R1 base is holonomic but not circular. A doorway may be feasible only
    at a different heading. Validate the complete swept footprint (2-degree
    samples with a half-cell raster margin), not just its end orientation.
    """
    import cv2
    import numpy as np
    from .gt_navigation import GridMap
    raw=np.frombuffer(raw_free,dtype='uint8').reshape(grid.height,grid.width)
    layers=collision_layers or [(raw,np.asarray(footprint,dtype=float))]
    layers=[(r,np.asarray(p,dtype=float)) for r,p in layers]
    cell=grid.cell(current)
    def rotated(points,degrees):
        a=math.radians(degrees);c,s=math.cos(a),math.sin(a)
        return points@np.array([[c,-s],[s,c]]).T
    def make(footprints):
        free=np.ones_like(raw)
        for (obstacles,_),points in zip(layers,footprints):
            kernel=footprint_kernel(points,grid.resolution)
            free &= cv2.erode(obstacles,kernel,borderType=cv2.BORDER_CONSTANT,borderValue=0)
        return GridMap(grid.width,grid.height,grid.resolution,grid.origin,free.tobytes())
    valid={1:True,-1:True};swept={sign:[[points] for _,points in layers] for sign in (1,-1)}
    for absolute in range(15,181,15):
        for sign in (1,-1):
            if not valid[sign]:continue
            if check_cancelled:check_cancelled()
            angles=np.linspace(sign*(absolute-15),sign*absolute,9)[1:]
            for entries,(_,points) in zip(swept[sign],layers):entries.extend(rotated(points,a) for a in angles)
            turn_grid=make([np.concatenate(entries) for entries in swept[sign]])
            if not turn_grid.navigable(cell):
                valid[sign]=False
                continue
            footprints=[rotated(points,sign*absolute) for _,points in layers]
            yield make(footprints),yaw+math.radians(sign*absolute),turn_grid,footprints[0].tolist()
