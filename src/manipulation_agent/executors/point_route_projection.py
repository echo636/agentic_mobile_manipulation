"""Project a floor click onto a checked forward-reachable endpoint.

An eight-heading roadmap is a fast route proposal, not the continuous planner's
orientation limit. Failure here falls back to continuous-pose planning. The
roadmap shares reachability work across all endpoint positions and headings;
endpoint projection happens after reachability, rather than fixing a nearby
holonomic endpoint before discovering that the forward route cannot reach it.
"""
from dataclasses import replace
import math


def prepare_point_projection():
    """Load the optional compiled graph dependency during simulator startup."""
    try:
        from scipy.sparse.csgraph import dijkstra
    except ImportError:
        return False
    return True


def project_forward_point(options, actual, collision, check_cancelled, *, max_endpoint_checks=128):
    import numpy as np
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import dijkstra
    from .forward_navigation import angle, checked_connection, local_exit_certificate

    if not hasattr(collision,'grid_at_heading') or not hasattr(collision,'_sweep_grid'):
        return None
    if hasattr(collision,'local_window'):
        points=[actual[:2],options[0].target,*[xy for p in options for xy in p.points],*[p.goal for p in options]]
        low=tuple(min(p[i] for p in points) for i in (0,1))
        high=tuple(max(p[i] for p in points) for i in (0,1))
        centre=tuple((a+b)/2 for a,b in zip(low,high))
        radius=max(b-a for a,b in zip(low,high))/2+1.2
        collision=collision.local_window(centre,radius)
        collision.check_cancelled=check_cancelled
    grid=collision.grid
    shape=(grid.height,grid.width)
    headings=tuple(i*math.pi/4 for i in range(8))
    free=[];indices=[];node_cells=[];node_headings=[];count=0
    for k,yaw in enumerate(headings):
        check_cancelled()
        mask=np.frombuffer(collision.grid_at_heading(yaw).free,dtype='uint8').reshape(shape).astype(bool)
        cells=np.flatnonzero(mask)
        index=np.full(shape,-1,dtype=np.int32)
        index.ravel()[cells]=np.arange(count,count+len(cells),dtype=np.int32)
        free.append(mask);indices.append(index)
        node_cells.append(cells);node_headings.append(np.full(len(cells),k,dtype='uint8'))
        count+=len(cells)
    # The graph starts at the measured continuous pose through fully checked
    # connectors. There is no unchecked snap to a cell or compass heading.
    sr,sc=grid.cell(actual[:2]);roots={}
    cells=sorted(((sr+dr,sc+dc) for dr in (-1,0,1) for dc in (-1,0,1)
                  if grid.contains((sr+dr,sc+dc))),key=lambda cell:math.dist(grid.world(cell),actual[:2]))
    for cell in cells:
        for k,yaw in enumerate(headings):
            check_cancelled()
            node=int(indices[k][cell])
            if node<0:continue
            poses=checked_connection(actual,grid.world(cell),angle(yaw),collision,check_cancelled)
            if poses is not None:roots[node]=poses
    if not roots:return None
    rows=[];cols=[];weights=[]
    for k,yaw in enumerate(headings):
        check_cancelled()
        dr,dc=round(math.sin(yaw)),round(math.cos(yaw))
        r0,r1=max(0,-dr),min(grid.height,grid.height-dr)
        c0,c1=max(0,-dc),min(grid.width,grid.width-dc)
        mask=free[k]
        valid=mask[r0:r1,c0:c1]&mask[r0+dr:r1+dr,c0+dc:c1+dc]
        if dr and dc:valid &= mask[r0:r1,c0+dc:c1+dc]&mask[r0+dr:r1+dr,c0:c1]
        source=indices[k][r0:r1,c0:c1][valid]
        rows.append(source);cols.append(indices[k][r0+dr:r1+dr,c0+dc:c1+dc][valid])
        weights.append(np.full(len(source),grid.resolution*math.hypot(dr,dc)))
        for offset in (-1,1):
            check_cancelled()
            other=(k+offset)%8
            swept=collision._sweep_grid(collision._key(yaw,yaw+offset*math.pi/4))
            valid=np.frombuffer(swept.free,dtype='uint8').reshape(shape).astype(bool)&mask&free[other]
            source=indices[k][valid]
            rows.append(source);cols.append(indices[other][valid]);weights.append(np.full(len(source),.375))
    rows.append(np.full(len(roots),count,dtype=np.int32));cols.append(np.array(list(roots),dtype=np.int32))
    weights.append(np.array([sum(math.dist(a[:2],b[:2])+.5*abs(angle(b[2]-a[2]))/(math.pi/3)
                                 for a,b in zip(poses,poses[1:])) for poses in roots.values()]))
    check_cancelled()
    graph=coo_matrix((np.concatenate(weights),(np.concatenate(rows),np.concatenate(cols))),
                     shape=(count+1,count+1)).tocsr()
    distances,parents=dijkstra(graph,directed=True,indices=count,return_predecessors=True)
    check_cancelled()
    node_cells=np.concatenate(node_cells);node_headings=np.concatenate(node_headings)
    reachable=np.flatnonzero(np.isfinite(distances[:-1]))
    target=options[0].target
    cells=node_cells[reachable]
    target_distances=np.hypot((cells%grid.width)*grid.resolution+grid.origin[0]-target[0],
                              (cells//grid.width)*grid.resolution+grid.origin[1]-target[1])
    order=np.lexsort((distances[reachable],np.round(target_distances,9)))
    for checked,index in enumerate(order[:max_endpoint_checks],1):
        check_cancelled()
        node=int(reachable[index]);cell=divmod(int(node_cells[node]),grid.width)
        goal=grid.world(cell);yaw=angle(headings[int(node_headings[node])]);pose=(*goal,yaw)
        entry=local_exit_certificate(pose,collision,incoming=True,check_cancelled=check_cancelled)
        if entry is None:continue
        departure=local_exit_certificate(pose,collision,check_cancelled=check_cancelled)
        if departure is None:continue
        chain=[node]
        while int(parents[chain[-1]])!=count:
            chain.append(int(parents[chain[-1]]))
        poses=list(roots[chain[-1]])
        for ancestor in reversed(chain[:-1]):
            xy=grid.world(divmod(int(node_cells[ancestor]),grid.width))
            poses.append((*xy,angle(headings[int(node_headings[ancestor])])))
        # Recheck the actual reconstructed trajectory, independently of the
        # vectorized graph construction and using execution's swept checker.
        if not all(collision.motion_free(a,b) for a,b in zip(poses,poses[1:])):continue
        distance=sum(math.dist(a[:2],b[:2]) for a,b in zip(poses,poses[1:]))
        chosen=replace(options[0],points=tuple(p[:2] for p in poses),goal=goal,final_yaw=yaw,
                       travel_yaw=yaw,score=float(target_distances[index]),geodesic_m=distance)
        return chosen,poses,{'search':'forward_reachable_point_projection','sampled_headings':8,
                            'continuous_pose_fallback':True,'graph_nodes':count,'graph_edges':graph.nnz,
                            'projection_window':getattr(collision,'window',None),
                            'reachable_poses':len(reachable),'endpoint_checks':checked,
                            'local_entry_certificate':entry,'local_exit_certificate':departure,
                            'target_distance_m':chosen.score,'globally_optimal':False}
    return None
