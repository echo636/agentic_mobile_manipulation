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


def clipped_body_projection(points, low, high):
    """XY projection of a link's convex collision hull inside a Z slab."""
    import numpy as np
    from scipy.spatial import ConvexHull, QhullError
    if points[:,2].max()<low or points[:,2].min()>high:return None
    clipped=list(points[(points[:,2]>=low)&(points[:,2]<=high),:2])
    try:
        hull=ConvexHull(points)
        edges={tuple(sorted((int(a),int(b)))) for face in hull.simplices
               for a,b in zip(face,np.roll(face,1))}
    except QhullError:
        # Degenerate planar geometry: all pairs cover every projected edge.
        edges={(i,j) for i in range(len(points)) for j in range(i)}
    for i,j in edges:
        a,b=points[i],points[j]
        if abs(float(b[2]-a[2]))<1e-12:continue
        for z in (low,high):
            t=(z-a[2])/(b[2]-a[2])
            if 0<=t<=1:clipped.append((a+t*(b-a))[:2])
    return np.asarray(clipped) if clipped else None


def build_navigation_grid(backend, floor, *, prospective_carry=(), robot_only=False):
    import cv2
    import numpy as np
    import omnigibson.lazy as lazy
    from .gt_navigation import GridMap
    scene=backend.env.scene;trav=scene.trav_map;resolution=float(trav.map_resolution)
    floor_z=float(trav.floor_heights[floor]);base=backend.robot.get_position_orientation()[0].cpu().numpy()
    # The base is wider than the torso. A single base-width column falsely
    # blocks motion beside desks/chair backs. Match occupancy by height band.
    held=backend._get_held()
    carried=[obj for obj in [held,*[o for o,_ in backend._carry_contents]] if obj is not None]
    carried.extend(obj for obj in prospective_carry if obj not in carried)
    moving_objects=[backend.robot,*([] if robot_only else carried)]
    height=max(float(obj.aabb[1][2])+(.18 if obj in prospective_carry else 0.) for obj in moving_objects)-floor_z
    # Stable, conservative band edges avoid a full raster rebuild for every
    # micrometre of pose-setter feedback after a navigation action.
    edges=[.03,.35,.8,1.25,max(1.26,math.ceil((height+.02)*100)/100)]
    bodies=[]
    carried_hulls=0
    for group,obj in enumerate(moving_objects):
        object_hulls=0
        for link in obj.links.values():
            if link.is_meta_link:continue
            points=link.collision_boundary_points_world
            if points is not None:
                vertices=points.cpu().numpy()
                if obj in prospective_carry:vertices=vertices+np.array([0.,0.,.18])
                bodies.append((group,vertices))
                object_hulls+=1
                if obj is not backend.robot:carried_hulls+=1
        if obj is not backend.robot and not object_hulls:
            raise ValueError('Carried collision geometry unavailable for navigation')
    layers=[]
    for group in range(len(moving_objects)):
        body_edges=edges
        if group:
            # Carry clearance uses the payload's actual Z extent. A plate
            # lifted above a counter must not inherit the counter's entire
            # 0.8--1.25 m occupancy slab. Quantization is outward, not inward.
            vertices=[p for g,p in bodies if g==group]
            low=max(.03,math.floor((min(float(p[:,2].min()) for p in vertices)-floor_z-.002)*100)/100)
            high=math.ceil((max(float(p[:,2].max()) for p in vertices)-floor_z+.002)*100)/100
            body_edges=[low,*[round(low+.1*i,8) for i in range(1,math.ceil((high-low)/.1))],high]
        for lo,hi in zip(body_edges,body_edges[1:]):
            # Keep disjoint robot / payload bodies disjoint. A convex hull across
            # them invents a solid bridge in the air between the base and payload.
            chunks=[xy-base[:2] for g,p in bodies if g==group and
                    (xy:=clipped_body_projection(p,floor_z+lo,floor_z+hi)) is not None]
            if not chunks:continue
            hull=cv2.convexHull(np.concatenate(chunks).astype('float32')).reshape(-1,2)
            layers.append((lo,hi,hull,group))
    if not layers:raise ValueError('Robot collision geometry unavailable')
    chassis=layers[0][2];kernel=footprint_kernel(chassis,resolution)
    source=Path(scene.scene_dir)/'layout'/f'floor_trav_no_obj_{floor}.png'
    cache=getattr(backend,'_live_gt_collision_cache',None)
    if cache is None:cache=backend._live_gt_collision_cache={}
    # Occupancy depends on height, not on which moving body uses that band.
    # Grasp preview / detach must not invalidate a whole-room raster merely
    # because the payload adds another footprint at an existing height.
    bands=list(dict.fromkeys((lo,hi) for lo,hi,_,_ in layers))
    band_indices=[bands.index((lo,hi)) for lo,hi,_,_ in layers]
    if floor not in cache:
        if not source.is_file():raise ValueError('GT floor support map unavailable: '+str(source))
        raw=cv2.imread(str(source),cv2.IMREAD_GRAYSCALE);h,w=trav.floor_map[floor].shape
        raw=(cv2.resize(raw,(w,h))==255).astype('uint8')
        cache[floor]={'support':raw,'band_cache':{},'objects':{},'height':None,'bands':bands,
                      'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest()}
    cached=cache[floor];support=cached['support'];h,w=support.shape
    origin=np.array([-w*resolution/2,-h*resolution/2])
    ignored_objects={backend.robot,*carried}
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
    query=lazy.omni.physx.get_physx_scene_query_interface()
    started=time.monotonic();count=0;updated_queries=0
    updated=np.zeros_like(support,dtype=bool);raw_bands=[]
    band_cache=cached.setdefault('band_cache',{})
    for low,high in bands:
        key=(low,high)
        entry=band_cache.get(key)
        dirty=support.astype(bool) if entry is None else np.zeros_like(support,dtype=bool)
        previous={} if entry is None else entry['objects']
        raw=support.copy() if entry is None else entry['free']
        for name in set(current)|set(previous):
            old,new=previous.get(name),current.get(name)
            if old is not None and new is not None and old['signature']==new['signature']:continue
            for record in (old,new):
                if record is None:continue
                lo=np.floor((record['lo']-origin)/resolution).astype(int)-1
                hi=np.ceil((record['hi']-origin)/resolution).astype(int)+2
                dirty[max(0,lo[1]):min(h,hi[1]),max(0,lo[0]):min(w,hi[0])]=True
        dirty &= support.astype(bool);updated |= dirty
        for row,col in np.argwhere(dirty):
            if updated_queries%2048==0:
                backend.deadline.check()
                local=getattr(backend,'_local_operation',None)
                if local is not None:local.check()
            xy=origin+np.array([col,row])*resolution
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
            raw[row,col]=0 if blocked else 1
            updated_queries+=1
        band_cache[key]={'free':raw,'objects':current}
        raw_bands.append(raw)
    # Bound memory without throwing away unchanged robot-height bands whenever
    # grasping/releasing changes the set of payload layers.
    for key in list(band_cache):
        if len(band_cache)<=32:break
        if key not in bands:del band_cache[key]
    free=np.stack(raw_bands);count=int(updated.sum())
    cached.update(objects=current,height=height,bands=bands,free=free)
    collision_raw=[free[index] for index in band_indices]
    navigable=support.copy();overlap=np.zeros_like(support,dtype='float32')
    for raw,(_,_,footprint,_) in zip(collision_raw,layers):
        k=footprint_kernel(footprint,resolution)
        navigable &= cv2.erode(raw,k,borderType=cv2.BORDER_CONSTANT,borderValue=0)
        overlap+=cv2.filter2D((1-raw).astype('float32'),-1,k.astype('float32'),borderType=cv2.BORDER_CONSTANT)
    grid=GridMap(w,h,resolution,tuple(origin),navigable.tobytes())
    # Use the same subcell/yaw reserve as forward route certification. Without
    # this, an old fixed-heading goal can sit on a boundary that the driving
    # controller must reject, and initial recovery would not see the mismatch.
    from .heading_collision import HeadingCollisionChecker
    grid=HeadingCollisionChecker(grid,[(raw,footprint) for raw,(_,_,footprint,_) in zip(collision_raw,layers)],
        0.,support,check_cancelled=backend.deadline.check).grid_at_heading(0.)
    combined=np.all(free,axis=0).astype('uint8')
    backend._navigation_clearance=(combined.tobytes(),overlap.ravel(),max(float(np.linalg.norm(p,axis=1).max()) for _,_,p,_ in layers)+resolution)
    backend._navigation_layers=[(raw.copy(),footprint.copy()) for raw,(_,_,footprint,_) in zip(collision_raw,layers)]
    backend._navigation_layer_heights=[(lo,hi) for lo,hi,_,_ in layers]
    backend._navigation_support=support.copy()
    return grid,{'source':str(source),'source_sha256':cached['source_sha256'],
        'chassis_footprint_world_offsets':chassis.tolist(),'kernel_shape':list(kernel.shape),
        'footprint':'current_yaw_height_matched_collision_hulls',
        'forward_clearance_reserve':'half_cell_translation_and_outward_quantized_yaw_sweep',
        'collision_layers':[{'z_min':lo,'z_max':hi,'footprint':p.tolist(),'body_group':group} for lo,hi,p,group in layers],
        'disjoint_payload_footprints':True,
        'occupancy_height_bands':len(bands),
        'query_height_m':height,'updated_cells':count,'updated_queries':updated_queries,'query_seconds':time.monotonic()-started,
        'dynamic_collision_check':'live_PhysX_height_matched_overlaps_incrementally_updated',
        'collision_substrate':'GT_floor_support_and_current_loaded_collision_geometry',
        'held_object_footprint_included':bool(carried_hulls),
        'carried_collision_hulls':carried_hulls}


def clearance_transition_guard(grid, collision_layers, support, heights=None):
    """Shed existing contacts without entering a new obstacle region.

    A non-increasing overlap count alone can exchange an old contact for a new
    one and permit crossing a wall. Require non-increasing overlap separately
    for each connected 3D obstacle, plus continuity of its contact
    patch. Sliding off a door can shift the contact pixels along that same leaf;
    an exact world-cell subset test would incorrectly forbid this retreat.
    Once the raw centre is free it cannot enter an occupied cell again. Floor
    support is mandatory even if the robot starts overlapped by a door.
    """
    import numpy as np
    from scipy.ndimage import label
    # Link components only across physically touching height intervals. Payload
    # bands may overlap robot bands or be far from their array neighbours.
    if heights is None:heights=[(i,i+1) for i in range(len(collision_layers))]
    adjacent=[[j for j,(c,d) in enumerate(heights) if max(a,c)<=min(b,d)+1e-8]
              for a,b in heights]
    labels=[];total=0
    for raw,_ in collision_layers:
        ids,n=label(raw==0,structure=np.ones((3,3)))
        labels.append(np.where(ids,ids+total,0));total+=n
    parent=list(range(total+1))
    def root(i):
        while parent[i]!=i:
            parent[i]=parent[parent[i]];i=parent[i]
        return i
    for i,neighbours in enumerate(adjacent):
        for j in neighbours:
            if j>=i:continue
            padded=np.pad(labels[j],1)
            for dr in range(3):
                for dc in range(3):
                    other=padded[dr:dr+grid.height,dc:dc+grid.width]
                    mask=(labels[i]>0)&(other>0)
                    pairs=np.unique(np.stack([labels[i][mask],other[mask]],axis=1),axis=0)
                    for a,b in pairs:parent[root(int(a))]=root(int(b))
    roots=np.asarray([root(i) for i in range(total+1)])
    labels=[roots[ids] for ids in labels]
    layers=[]
    for index,(raw,footprint) in enumerate(collision_layers):
        kernel=footprint_kernel(footprint,grid.resolution)
        offsets=np.argwhere(kernel)-np.array(kernel.shape)//2
        layers.append((raw,offsets,labels[index]))
    cache={}
    def contacts(cell):
        if cell not in cache:
            hits=set();counts={}
            for index,(raw,offsets,labels) in enumerate(layers):
                for dr,dc in offsets:
                    r,c=cell[0]+int(dr),cell[1]+int(dc)
                    if not grid.contains((r,c)):
                        cache[cell]=None
                        return None
                    if not raw[r,c]:
                        hits.add((index,r,c))
                        key=int(labels[r,c]);counts[key]=counts.get(key,0)+1
            cache[cell]=(hits,counts)
        return cache[cell]
    def allowed(previous,current):
        if not layers or not grid.contains(current) or not support[current]:return False
        before=contacts(previous);after=contacts(current)
        if before is None or after is None:return False
        if all(raw[previous] for raw,_,_ in layers) and not all(raw[current] for raw,_,_ in layers):return False
        old,old_counts=before;new,new_counts=after
        if any(count>old_counts.get(key,0) for key,count in new_counts.items()):return False
        # No new, spatially separate contact patch on a connected wall/table.
        return all(any((other,r+dr,c+dc) in old
                       for other in adjacent[layer] for dr in (-1,0,1) for dc in (-1,0,1))
                   for layer,r,c in new-old)
    return allowed


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


def rotation_staging_plan(grid, collision_layers, current, yaw, *, check_cancelled=None):
    """Reach the nearest place where the body can turn before a narrow route."""
    import cv2
    import numpy as np
    from dataclasses import replace
    from .gt_navigation import plan_navigation, NavigationError
    free=np.ones((grid.height,grid.width),dtype='uint8')
    for obstacles,footprint in collision_layers:
        radius=float(np.linalg.norm(footprint,axis=1).max())
        angles=np.linspace(0,2*math.pi,181)
        circle=np.column_stack((np.cos(angles),np.sin(angles)))*(radius+grid.resolution)
        free &= cv2.erode(obstacles,footprint_kernel(circle,grid.resolution),borderType=cv2.BORDER_CONSTANT,borderValue=0)
    available=np.frombuffer(grid.free,dtype='uint8').reshape(grid.height,grid.width)
    labels=cv2.connectedComponents(available,connectivity=4)[1];label=labels[grid.cell(current)]
    if not label:raise NavigationError('No reachable turning location')
    rows,cols=np.where(free & (labels==label))
    if not len(rows):raise NavigationError('No reachable turning location')
    xy=np.column_stack((cols,rows))*grid.resolution+np.array(grid.origin)
    goal=xy[np.argmin(np.linalg.norm(xy-np.array(current),axis=1))]
    if np.linalg.norm(goal-current)<grid.resolution:raise NavigationError('No additional turning clearance available')
    plan=plan_navigation(grid,current,goal,goal_mode='point',max_snap=0.,check_cancelled=check_cancelled)
    return replace(plan,travel_yaw=yaw,final_yaw=yaw)
