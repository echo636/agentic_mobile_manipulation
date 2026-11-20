"""Current-pose, per-collider overlap ordering; never a rejection certificate."""
import numpy as np

def oriented_piece_box(points):
    """Enclose one active collider in principal axes, in the root frame.

    This enclosure is only for ordering proposals. Empty space inside a
    concave collider or this box must never be a placement rejection reason.
    """
    mean=points.mean(axis=0)
    _,_,axes=np.linalg.svd(points-mean,full_matrices=False)
    if axes.shape!=(3,3):return None
    rotation=axes.T
    if np.linalg.det(rotation)<0:rotation[:,-1]*=-1
    local=(points-mean)@rotation;low,high=local.min(axis=0),local.max(axis=0)
    return mean+rotation@((low+high)/2),np.maximum((high-low)/2,1e-6),rotation


def assembly_overlap_ranker(backend, held, assembly):
    import omnigibson.lazy as lazy
    from omnigibson.utils import transform_utils as T
    root_position,root_quat=held.get_position_orientation()
    origin=root_position.cpu().numpy();root_rotation=T.quat2mat(root_quat).cpu().numpy()
    pieces=[]
    for obj in assembly:
        for link in obj.links.values():
            if link.is_meta_link or getattr(link,'visual_only',False):continue
            apis=getattr(link,'_collision_apis',())
            if apis and not any(api.GetCollisionEnabledAttr().Get() for api in apis):continue
            for mesh in getattr(link,'collision_meshes',{}).values():
                points=mesh.points.cpu().numpy()
                if not len(points):continue
                matrix=mesh.scaled_transform.cpu().numpy()
                points=(points@matrix[:3,:3].T+matrix[:3,3]-origin)@root_rotation
                if not np.isfinite(points).all():raise ValueError('Invalid collision vertices for proposal ordering')
                box=oriented_piece_box(points)
                if box is not None:pieces.append(box)
    if not pieces:return None
    ignored={str(link.prim_path) for obj in [backend.robot,*assembly] for link in obj.links.values()}
    ignored.update(str(link.prim_path) for obj in backend.env.scene.objects for link in obj.links.values() if link.is_meta_link)
    query=lazy.omni.physx.get_physx_scene_query_interface();cache={};stats=dict(poses=0,queries=0,pieces=len(pieces))
    def score(position,quaternion):
        pos=np.asarray(position,dtype=float)
        quat=quaternion if backend.torch.is_tensor(quaternion) else backend.torch.tensor(quaternion)
        key=tuple(round(float(v),5) for v in (*pos,*quat.cpu().tolist()))
        if key not in cache:
            stats['poses']+=1;rotation=T.quat2mat(quat).cpu().numpy();collision=False
            def hit(row):
                nonlocal collision
                if str(row.rigid_body) in ignored:return True
                collision=True;return False
            for center,half,axes in pieces:
                backend.deadline.check(changed=True)
                box_quat=T.mat2quat(backend.torch.tensor(rotation@axes,dtype=backend.torch.float32)).cpu().tolist()
                stats['queries']+=1
                query.overlap_box(tuple(map(float,half)),tuple(map(float,pos+rotation@center)),tuple(box_quat),hit,False)
                if collision:break
            cache[key]=int(collision)
        return cache[key]
    score.stats=stats
    return score
