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
    chassis=[]
    for link in backend.robot.links.values():
        if link.is_meta_link:continue
        points=link.collision_boundary_points_world
        if points is None:continue
        points=points.cpu().numpy()
        if points[:,2].min()<floor_z+.20:
            low=points[points[:,2]<=floor_z+.30]
            if len(low):chassis.extend(low[:,:2]-base[:2])
    if not chassis:raise ValueError('Robot chassis collision geometry unavailable')
    kernel=footprint_kernel(chassis,resolution)
    # Holonomic translation preserves the current chassis yaw all along the
    # route, so the actual oriented footprint applies without a giant disk.
    height=float(backend.robot.aabb[1][2])-floor_z
    source=Path(scene.scene_dir)/'layout'/f'floor_trav_no_obj_{floor}.png'
    cache=getattr(backend,'_live_gt_collision_cache',None)
    if cache is None:cache=backend._live_gt_collision_cache={}
    if floor not in cache:
        if not source.is_file():raise ValueError('GT floor support map unavailable: '+str(source))
        raw=cv2.imread(str(source),cv2.IMREAD_GRAYSCALE);h,w=trav.floor_map[floor].shape
        raw=(cv2.resize(raw,(w,h))==255).astype('uint8')
        cache[floor]={'support':raw,'free':raw.copy(),'objects':{},'height':None,
                      'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest()}
    cached=cache[floor];support=cached['support'];free=cached['free'];h,w=free.shape
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
        if hi[2]<floor_z+.03 or lo[2]>floor_z+height:continue
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
        blocked=False;query_error=None
        def hit_callback(hit):
            nonlocal blocked,query_error
            try:
                # PhysX all-hit callbacks receive OverlapHit objects; only
                # raycast_closest returns a dictionary. Never treat a callback
                # exception swallowed by the native boundary as empty space.
                if str(hit.rigid_body) not in ignored_links:
                    blocked=True
                    return False
                return True
            except Exception as exc:
                query_error=exc
                return False
        xy=origin+np.array([col,row])*resolution
        # A volume overlap also catches a thin closed leaf or a column whose
        # origin is already inside a solid; a one-sided vertical ray can miss it.
        query.overlap_box((resolution/2,resolution/2,max(.05,(height-.03)/2)),
                          (float(xy[0]),float(xy[1]),floor_z+(height+.03)/2),
                          (0.,0.,0.,1.),hit_callback,False)
        if query_error is not None:raise RuntimeError('PhysX overlap callback failed') from query_error
        free[row,col]=0 if blocked else 1
        count+=1
    cached.update(objects=current,height=height)
    navigable=cv2.erode(free,kernel,borderType=cv2.BORDER_CONSTANT,borderValue=0)
    grid=GridMap(w,h,resolution,tuple(origin),navigable.tobytes())
    # Preserve overlap amount for a local retreat when an opened door intrudes
    # into the existing footprint. The base centre must still be obstacle-free.
    overlap=cv2.filter2D((1-free).astype('float32'),-1,kernel.astype('float32'),borderType=cv2.BORDER_CONSTANT)
    backend._navigation_clearance=(free.tobytes(),overlap.ravel(),float(np.linalg.norm(chassis,axis=1).max())+resolution)
    hull=cv2.convexHull(np.asarray(chassis,dtype='float32')).reshape(-1,2).tolist()
    return grid,{'source':str(source),'source_sha256':cached['source_sha256'],
        'chassis_footprint_world_offsets':hull,'kernel_shape':list(kernel.shape),
        'footprint':'current_yaw_chassis_convex_hull_holonomic_translation',
        'query_height_m':height,'updated_cells':count,'query_seconds':time.monotonic()-started,
        'dynamic_collision_check':'live_PhysX_overlap_columns_incrementally_updated',
        'collision_substrate':'GT_floor_support_and_current_loaded_collision_geometry',
        'held_object_footprint_included':False}
