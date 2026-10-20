"""OmniGibson adapter for local manipulation; GT stays executor-private."""
from dataclasses import asdict
import json
import math
from ..contracts import SkillError
from ..records import now
from .local_manipulation import LocalLimits, LocalOperation, stance_candidates
from .visual_target import LockedTarget


class LocalRGBManipulation:
    def _local_pose(self):
        from .visual_target import rotate
        pos,quat=self.robot.get_position_orientation()
        forward=rotate((1.,0.,0.),quat)
        return float(pos[0]),float(pos[1]),math.atan2(forward[1],forward[0])

    def _local_record(self, **data):
        with (self.output/'local_manipulation.jsonl').open('a') as stream:
            stream.write(json.dumps({'at':now(),'parent_request_id':getattr(self,'_active_request_id',None),
                                    'env_step':self.steps,'audience':'executor_private',**data})+'\n')

    def _resolve_local_target(self,binding):
        obj,point=binding.resolve(self.env.scene.objects,self.local_limits.max_target_drift_m)
        base=self.robot.get_position_orientation()[0]
        return obj,self.torch.tensor(point,dtype=base.dtype,device=base.device)

    def _local_geometry(self,operation):
        from .live_gt_map import build_navigation_grid
        from .heading_collision import HeadingCollisionChecker
        position=self.robot.get_position_orientation()[0]
        trav=self.env.scene.trav_map
        floor=min(range(len(trav.floor_heights)),key=lambda i:abs(float(position[2])-trav.floor_heights[i]))
        grid,details=build_navigation_grid(self,floor)
        self._navigation_layer_yaw=self._local_pose()[2]
        collision=HeadingCollisionChecker(grid,self._navigation_layers,self._navigation_layer_yaw,
                                          self._navigation_support,check_cancelled=operation.check)
        hulls,_=self._navigation_self_hulls()
        return grid,collision,details,hulls,floor

    def _local_grasp_preview(self,obj,operation,geometry):
        """Predict ideal grasp's +18 cm lift and the WHOLE rigid payload.

        No simulator setter or grasp is executed. Candidate-specific geometry
        rotates the robot but leaves the not-yet-grasped target in world space.
        """
        import numpy as np
        import cv2
        from .live_gt_map import build_navigation_grid, clipped_body_projection
        from .heading_collision import HeadingCollisionChecker
        payload=[obj,*[child for child,_,_ in self._grasp_dependencies(obj)]]
        names=('_navigation_layers','_navigation_support','_navigation_clearance','_navigation_layer_yaw')
        saved={name:getattr(self,name) for name in names}
        try:
            grid,details=build_navigation_grid(self,geometry[4],prospective_carry=payload)
            raw=[layer[0] for layer in self._navigation_layers]
            support=self._navigation_support
        finally:
            for name,value in saved.items():setattr(self,name,value)
        robot_vertices=[link.collision_boundary_points_world.cpu().numpy()
                        for link in self.robot.links.values()
                        if not link.is_meta_link and link.collision_boundary_points_world is not None]
        payload_vertices=[[link.collision_boundary_points_world.cpu().numpy()+np.array([0.,0.,.18])
                          for link in item.links.values()
                          if not link.is_meta_link and link.collision_boundary_points_world is not None]
                          for item in payload]
        base=self._local_pose()
        floor_z=float(self.env.scene.trav_map.floor_heights[geometry[4]])
        def checker_at(pose):
            operation.check()
            delta=pose[2]-base[2];c,s=math.cos(delta),math.sin(delta)
            bodies=[]
            for vertices in robot_vertices:
                moved=vertices.copy()
                moved[:,:2]=(moved[:,:2]-base[:2])@np.array([[c,s],[-s,c]])+pose[:2]
                bodies.append(moved)
            layers=[]
            for occupancy,band in zip(raw,details['collision_layers']):
                group=[bodies,*payload_vertices][band['body_group']]
                chunks=[xy-np.asarray(pose[:2]) for vertices in group
                        if (xy:=clipped_body_projection(vertices,floor_z+band['z_min'],floor_z+band['z_max'])) is not None]
                if chunks:
                    footprint=cv2.convexHull(np.concatenate(chunks).astype('float32')).reshape(-1,2)
                    layers.append((occupancy,footprint))
            return HeadingCollisionChecker(grid,layers,pose[2],support,check_cancelled=operation.check)
        return checker_at

    def _local_ready(self,primitive,obj,point,operation):
        from .forward_navigation import local_exit_certificate
        pose=self._local_pose()
        if math.dist(pose[:2],point[:2])>operation.limits.reach_m:return False
        if self._get_held() is obj and primitive in {'grasp','toggle_on','toggle_off'}:return True
        geometry=self._local_geometry(operation)
        operation.geometry=geometry
        if not geometry[1].motion_free(pose,pose):return False
        if not self._approach_visible(pose[:2],point,obj,margin=.04,self_hulls=geometry[3],base_yaw=pose[2]):return False
        if primitive=='grasp' and self.ideal_carry and not self.demo_motion:
            operation.grasp_preview=self._local_grasp_preview(obj,operation,geometry)
            if local_exit_certificate(pose,operation.grasp_preview(pose),check_cancelled=operation.check) is None:
                operation.record('check_departure',status='rejected',reason='no_loaded_departure')
                return False
        return True

    def _local_reposition(self,primitive,obj,point,operation):
        from .gt_navigation import NavigationPlan, NavigationError, PlanningBudget, SearchBudgetExhausted
        from .forward_navigation import plan_forward_route, local_exit_certificate
        from .local_manipulation import LocalCollision
        geometry=getattr(operation,'geometry',None) or self._local_geometry(operation)
        grid,collision,details,hulls,_=geometry
        actual=self._local_pose()
        if not collision.motion_free(actual,actual):
            raise SkillError('local_adjustment_blocked','Current body/carry footprint has no collision clearance')
        preview=getattr(operation,'grasp_preview',None)
        if primitive=='grasp' and self.ideal_carry and not self.demo_motion and preview is None:
            preview=self._local_grasp_preview(obj,operation,geometry)
        local=LocalCollision(collision,operation.origin,operation.limits.radius_m)
        budget=PlanningBudget(operation.check,seconds=operation.limits.planning_seconds)
        collision.check_cancelled=budget.check
        selected=None;attempts=[];exhausted=False;extended=False
        try:
            for pose in stance_candidates(actual,operation.origin,point,operation.limits,operation.visited):
                budget.check()
                row={'pose':pose,'status':'checking'};attempts.append(row)
                if not local.motion_free(pose,pose):row.update(status='rejected',reason='collision');continue
                if not self._approach_visible(pose[:2],point,obj,margin=.04,self_hulls=hulls,base_yaw=pose[2]):
                    row.update(status='rejected',reason='target_not_visible');continue
                if local_exit_certificate(pose,collision,check_cancelled=budget.check) is None:
                    row.update(status='rejected',reason='no_departure');continue
                if preview is not None:
                    loaded=preview(pose);loaded.check_cancelled=budget.check
                    if local_exit_certificate(pose,loaded,check_cancelled=budget.check) is None:
                        row.update(status='rejected',reason='no_loaded_departure');continue
                plan=NavigationPlan((actual[:2],pose[:2]),pose[:2],tuple(map(float,point[:2])),pose[2],
                    math.dist(actual[:2],pose[:2]),math.dist(pose[:2],point[:2]),len(attempts),1,0.,0,pose[2],'forward')
                per_route=PlanningBudget(budget.check,seconds=2.)
                collision.check_cancelled=per_route.check
                try:route=plan_forward_route(plan,actual,local,check_cancelled=per_route.check,max_expansions=3000)
                except NavigationError as exc:
                    row.update(status='rejected',reason=exc.code,detail=str(exc))
                    if exc.code!='navigation_search_exhausted':continue
                    exhausted=True
                    # One promising, geometrically admitted stance gets a
                    # larger retry; all work still shares the outer budget.
                    if extended:continue
                    extended=True;row['extended_route_search']=True
                    per_route=PlanningBudget(budget.check,seconds=4.)
                    collision.check_cancelled=per_route.check
                    try:route=plan_forward_route(plan,actual,local,check_cancelled=per_route.check,max_expansions=10000)
                    except NavigationError as retry:
                        row.update(reason=retry.code,detail=str(retry));continue
                finally:collision.check_cancelled=budget.check
                if route.distance_m+operation.path>operation.limits.path_m:
                    row.update(status='rejected',reason='path_budget');continue
                row['status']='selected';selected=(plan,route);break
        except SearchBudgetExhausted:
            exhausted=True
            operation.record('planning_limit',candidate_attempts=len(attempts))
        finally:
            collision.check_cancelled=operation.check
            operation.record('candidates',candidates=attempts,held_footprint=details['held_object_footprint_included'])
        if selected is None:
            if exhausted:
                raise SkillError('local_adjustment_exhausted','Local route search incomplete within planning budget')
            raise SkillError('local_adjustment_blocked','No reachable visible manipulation stance within local limits')
        plan,route=selected
        operation.record('move',route=asdict(route))
        result=self._execute_gt_plan(grid,plan,operation.step_limit-(self.steps-operation.start_steps),prepared_route=route)
        self.navigation_distance+=result['actual_path_distance_m']
        # State and geometry must be measured again after physics, even if a
        # route was certified before executing it.
        operation.geometry=None
        operation.grasp_preview=None

    def _local_reobserve(self,binding):
        self._render_rgb_views(require_calibration=True)
        self._resolve_local_target(binding)

    def _local_departure(self,operation,obj):
        from .forward_navigation import local_exit_certificate
        # A committed operation may open a door or change carried geometry.
        # Check actual post-state but do not repeat/undo its successful effect.
        try:
            operation.check()
            geometry=self._local_geometry(operation)
            certificate=local_exit_certificate(self._local_pose(),geometry[1],check_cancelled=operation.check)
            return 'checked' if certificate is not None else 'blocked'
        except SkillError as exc:
            if exc.code in {'episode_cancelled','episode_timeout'}:return 'not_checked_cancelled'
            return 'not_checked_budget' if exc.code=='local_adjustment_exhausted' else 'blocked'

    def _execute_local_manipulation(self,primitive,obj,point,grounding,max_steps,options):
        binding=LockedTarget.bind(obj,point,grounding)
        operation=LocalOperation(self,primitive,binding,max_steps,options)
        self._local_operation=operation
        try:return operation.run()
        finally:self._local_operation=None
