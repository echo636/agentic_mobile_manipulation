"""Bounded translation of an already-held assembly into a compact carry pose.

This remains an ideal motor operation, not an arm IK solver. Every swept box
includes the robot in collision queries; only the payload and meta links are
ignored. A blocked packing motion preserves the successful grasp.
"""
import math
import time
from ..contracts import SkillError


def compact_translation(origin, base, bounds, clear, *, check=lambda: None):
    """Read-only route proposal. Bounds are each link's world AABB."""
    radius = max(math.hypot(x-origin[0], y-origin[1]) for lo,hi in bounds
                 for x in (lo[0],hi[0]) for y in (lo[1],hi[1]))
    reach = max(.55, .42+radius+.06)
    if reach > .95:
        return None, 'payload_too_large'
    distance = math.dist(origin[:2], base[:2])
    if distance <= reach+.05:
        return (), 'already_compact'
    bottom = min(lo[2]-origin[2] for lo,hi in bounds)
    direction = math.atan2(origin[1]-base[1], origin[0]-base[0])
    # Preserve current side first; a front pose must not pull through the body.
    for height in (.65, .95):
        for angle in (direction, base[3], base[3]+math.pi/2, base[3]-math.pi/2):
            check()
            end = (base[0]+reach*math.cos(angle), base[1]+reach*math.sin(angle),
                   max(origin[2], base[2]+height-bottom))
            raised = (origin[0],origin[1],end[2])
            for route in ((origin,end),(origin,raised,end)):
                if sum(math.dist(a,b) for a,b in zip(route,route[1:])) > 2.:
                    continue
                if all(clear(a,b) for a,b in zip(route,route[1:])):
                    return route, 'planned'
    return None, 'blocked'


def translation_samples(route, step_m):
    for a,b in zip(route,route[1:]):
        count = max(1,math.ceil(math.dist(a,b)/step_m))
        for i in range(1,count+1):
            yield tuple(x+(y-x)*i/count for x,y in zip(a,b))


class CompactCarry:
    def _execute_prepared_carry(self, obj, plan, max_steps):
        """Execute precisely the admitted route, checking actual geometry anew."""
        from omnigibson.utils import transform_utils as T
        from .carry_geometry import PayloadSweep
        route=plan['route'];before=self.steps
        step=.5*self.og.sim.get_sim_step_dt()
        samples=sum(max(1,math.ceil(math.dist(a,b)/step)) for a,b in zip(route,route[1:]))
        if samples+6>max_steps:
            raise SkillError('local_adjustment_exhausted','Prepared carry route exceeds remaining step budget',changed=True)
        local=getattr(self,'_local_operation',None)
        check=local.check if local is not None else self.deadline.check
        payload=[obj,*[child for child,_ in self._carry_contents]]
        sweep=PayloadSweep(self,obj,payload,self._local_pose(),check=check)
        position,orientation=obj.get_position_orientation()
        if math.dist(position.cpu().tolist(),route[0])>.01:
            raise SkillError('target_changed','Payload moved after carry preparation',changed=True)
        base_pos,base_quat=self.robot.get_position_orientation()
        with self._anchored_operation():
            for number,(a,b) in enumerate(zip(route,route[1:])):
                previous=a
                for xyz in translation_samples((a,b),step):
                    check()
                    if not sweep.clear(previous,xyz,extraction=number==0,
                                       require_clear=number!=0 or math.dist(xyz,b)<1e-8):
                        self._carry_record(status='prepared_carry_blocked',diagnostic=sweep.last_failure)
                        raise SkillError('local_adjustment_blocked','Prepared payload route changed during execution',changed=True)
                    new=position.clone();new[:]=self.torch.tensor(xyz,dtype=new.dtype,device=new.device)
                    self._carry_relative=T.relative_pose_transform(new,orientation,base_pos,base_quat)
                    self._carry_follow()
                    self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
                    previous=xyz
            for _ in range(6):self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
        report={'status':plan['status'],'shared_prediction_and_execution':True,
                'steps':self.steps-before,'planned_route':route,'collision_queries':sweep.queries}
        self._carry_record(status='carry_posture',object=obj.name,posture=report)
        return report

    def _compact_carry(self, max_steps):
        import numpy as np
        import omnigibson.lazy as lazy
        from omnigibson.utils import transform_utils as T
        obj=self._get_held()
        if obj is None or getattr(self,'demo_motion',False):return {'status':'not_applicable'}
        payload=[obj,*[child for child,_ in self._carry_contents]]
        position,orientation=obj.get_position_orientation()
        base_pos,base_quat=self.robot.get_position_orientation()
        rotation=T.quat2mat(base_quat)
        yaw=math.atan2(float(rotation[1,0]),float(rotation[0,0]))
        origin=tuple(map(float,position));bounds=[]
        for item in payload:
            for link in item.links.values():
                if link.is_meta_link:continue
                points=link.collision_boundary_points_world
                if points is None:return {'status':'geometry_unavailable'}
                points=points.cpu().numpy()
                if not len(points) or not np.isfinite(points).all():return {'status':'geometry_unavailable'}
                bounds.append((points.min(axis=0),points.max(axis=0)))
        if not bounds:return {'status':'geometry_unavailable'}
        ignored={str(link.prim_path) for item in payload for link in item.links.values()}
        ignored.update(str(link.prim_path) for item in self.env.scene.objects
                       for link in item.links.values() if link.is_meta_link)
        query=lazy.omni.physx.get_physx_scene_query_interface()
        started=time.monotonic();before=self.steps;checks=0
        def check():
            self.deadline.check(changed=True)
            local=getattr(self,'_local_operation',None)
            if local is not None:local.check(changed=True)
        def clear(a,b):
            nonlocal checks
            check();checks+=1
            # A swept AABB conservatively contains every point on the whole
            # translation segment. Do not merely check its endpoints.
            da=np.asarray(a)-origin;db=np.asarray(b)-origin
            for lo,hi in bounds:
                low=np.minimum(lo+da,lo+db)-.005
                high=np.maximum(hi+da,hi+db)+.005
                blocked=False;query_error=None
                def hit(hit):
                    nonlocal blocked,query_error
                    try:
                        if str(hit.rigid_body) not in ignored:blocked=True;return False
                        return True
                    except Exception as exc:
                        query_error=exc;return False
                query.overlap_box(tuple((high-low)/2),tuple((high+low)/2),(0.,0.,0.,1.),hit,False)
                if query_error is not None:raise RuntimeError('Carry collision query failed') from query_error
                if blocked:return False
            return True
        def planning_check():
            check()
            if time.monotonic()-started>8. or checks>=80:
                raise SkillError('local_adjustment_exhausted','Carry posture planning budget reached',changed=True)
        report={'status':'planning','physical_arm_control':False,'offset_before_m':math.dist(origin[:2],base_pos[:2])}
        try:
            route,status=compact_translation(origin,(*map(float,base_pos),yaw),bounds,clear,check=planning_check)
            report['status']=status
            if route:
                samples=list(translation_samples(route,.5*self.og.sim.get_sim_step_dt()))
                if len(samples)>min(max_steps,180):
                    report['status']='not_moved_step_budget'
                else:
                    report['planned_distance_m']=sum(math.dist(a,b) for a,b in zip(route,route[1:]))
                    previous=origin
                    with self._anchored_operation():
                        for xyz in samples:
                            check()
                            if not clear(previous,xyz):
                                report['status']='stopped_collision';break
                            new=position.clone()
                            new[:]=self.torch.tensor(xyz,dtype=new.dtype,device=new.device)
                            self._carry_relative=T.relative_pose_transform(new,orientation,base_pos,base_quat)
                            self._carry_follow()
                            self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
                            previous=xyz
                        else:report['status']='packed'
        except SkillError as exc:
            # The grasp effect is committed. Stop promptly, report interrupted
            # packing, and never repeat or detach that successful grasp.
            report.update(status='stopped_budget_or_cancel',error_code=exc.code)
        report.update(steps=self.steps-before,wall_seconds=time.monotonic()-started,
                      collision_checks=checks,offset_after_m=math.dist(obj.get_position_orientation()[0][:2],base_pos[:2]))
        self._carry_record(status='carry_posture',object=obj.name,**{'posture':report})
        return report
