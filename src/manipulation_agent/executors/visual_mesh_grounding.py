"""Private selected-pixel ray queries against visible geometry, without Replicator IDs."""
from ..contracts import SkillError

def query_visual_surface(backend, start, direction, point):
    """Only the exact selected ray and measured depth participate in ownership.

    Object AABBs are a spatial broad phase, never semantic target selection.
    Local rigid mesh BVHs are cached; live link transforms include articulation.
    """
    import numpy as np
    from omnigibson.utils.usd_utils import mesh_prim_to_trimesh_mesh
    origin=start.cpu().numpy();vector=direction.cpu().numpy();depth_point=point.cpu().numpy()
    tolerance=max(.015,.005*float(np.linalg.norm(depth_point-origin)))
    cache=getattr(backend,'_visual_mesh_cache',None)
    if cache is None:cache=backend._visual_mesh_cache={}
    matches=[];checked=0
    for obj in backend.env.scene.objects:
        if obj is backend.robot:continue
        lo,hi=obj.aabb
        if not bool(((point>=lo.cpu()-.12)&(point<=hi.cpu()+.12)).all()):continue
        best=None
        for link in obj.links.values():
            for mesh in link.visual_meshes.values():
                if not mesh.visible:continue
                transform=mesh.scaled_transform.cpu().numpy()
                inverse=np.linalg.inv(transform)
                local_origin=(inverse@np.r_[origin,1.])[:3]
                local_vector=inverse[:3,:3]@vector
                path=mesh.prim_path
                if path not in cache:
                    cache[path]=mesh_prim_to_trimesh_mesh(mesh.prim,include_normals=False,include_texcoord=False)
                geometry=cache[path]
                locations,_,_=geometry.ray.intersects_location([local_origin],[local_vector],multiple_hits=True)
                checked+=1
                if not len(locations):continue
                world=locations@transform[:3,:3].T+transform[:3,3]
                gaps=np.linalg.norm(world-depth_point,axis=1)
                index=int(gaps.argmin());gap=float(gaps[index])
                if best is None or gap<best[0]:best=(gap,path,world[index].tolist())
        if best is not None:matches.append((best[0],obj,best[1],best[2]))
    matches.sort(key=lambda row:row[0])
    if not matches or matches[0][0]>tolerance:
        raise SkillError('invalid_visual_target','No visual triangle on the selected ray agrees with the selected depth')
    if len(matches)>1 and matches[1][0]-matches[0][0]<.001:
        raise SkillError('invalid_visual_target','Selected pixel lies on ambiguous touching visual surfaces')
    gap,obj,path,hit=matches[0]
    return obj,{'visual_mesh':path,'visual_triangle_position':hit,
                'visual_depth_agreement_error_m':gap,'visual_depth_tolerance_m':tolerance,
                'visual_meshes_tested':checked,'method':'same_pixel_depth_visual_triangle_ray'}
