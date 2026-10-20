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
        """Plan the same lift/packing route used by actual ideal grasp."""
        from .live_gt_map import build_navigation_grid
        from .heading_collision import HeadingCollisionChecker
        from .carry_geometry import PayloadSweep, LoadedCollision, plan_lift_and_compact
        payload=[obj,*[child for child,_,_ in self._grasp_dependencies(obj)]]
        names=('_navigation_layers','_navigation_support','_navigation_clearance','_navigation_layer_yaw','_navigation_layer_heights')
        saved={name:getattr(self,name) for name in names}
        try:
            grid,_=build_navigation_grid(self,geometry[4],prospective_carry=payload,robot_only=True)
            robot_collision=HeadingCollisionChecker(grid,self._navigation_layers,self._local_pose()[2],
                self._navigation_support,check_cancelled=operation.check)
        finally:
            for name,value in saved.items():setattr(self,name,value)
        base_z=float(self.robot.get_position_orientation()[0][2])
        cache={}
        def checker_at(pose):
            check=getattr(operation,'preview_check',operation.check)
            check()
            key=tuple(round(float(v),6) for v in pose)
            if key in cache:return cache[key]
            sweep=PayloadSweep(self,obj,payload,pose,check=check)
            route,status=plan_lift_and_compact(sweep.origin,(*pose[:2],base_z,pose[2]),
                                              sweep.bounds,sweep.clear,check=check)
            if route is None:
                operation.preview_failure={'reason':status,**(sweep.last_failure or {})}
                return None
            checker=LoadedCollision(robot_collision,sweep,pose,route)
            checker.carry_status=status
            cache[key]=checker
            return checker
        return checker_at

    def _admit_loaded_departure(self,pose,preview,operation,check):
        from .forward_navigation import local_exit_certificate
        operation.preview_check=check
        loaded=preview(pose)
        if loaded is None:return False
        loaded.check_cancelled=check
        if hasattr(loaded,'sweep'):loaded.sweep.check=check
        if hasattr(loaded,'robot_collision'):loaded.robot_collision.check_cancelled=check
        if local_exit_certificate(pose,loaded,check_cancelled=check) is None:
            if hasattr(loaded,'sweep'):
                operation.preview_failure=loaded.sweep.last_failure or {'stage':'loaded_departure'}
            return False
        if hasattr(loaded,'route'):
            operation.carry_plan={'pose':tuple(pose),'route':loaded.route,'status':loaded.carry_status}
        return True

    def _local_ready(self,primitive,obj,point,operation):
        from .forward_navigation import local_exit_certificate
        pose=self._local_pose()
        if math.dist(pose[:2],point[:2])>operation.limits.reach_m:return False
        if self._get_held() is obj and primitive in {'grasp','toggle_on','toggle_off'}:return True
        geometry=self._local_geometry(operation)
        operation.geometry=geometry
        if not geometry[1].motion_free(pose,pose):return False
        if not self._approach_visible(pose[:2],point,obj,margin=.04,self_hulls=geometry[3],base_yaw=pose[2]):return False
        if primitive in {'open','close'} and self.ideal_carry and not self.demo_motion:
            if not self._admit_articulation(primitive,obj,pose,operation,operation.check):return False
        if primitive=='grasp' and self.ideal_carry and not self.demo_motion:
            operation.grasp_preview=self._local_grasp_preview(obj,operation,geometry)
            if not self._admit_loaded_departure(pose,operation.grasp_preview,operation,operation.check):
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
            self._local_clearance_recovery(operation,geometry)
            return
        preview=getattr(operation,'grasp_preview',None)
        if primitive=='grasp' and self.ideal_carry and not self.demo_motion and preview is None:
            preview=self._local_grasp_preview(obj,operation,geometry)
        if primitive in {'open','close'} and self.ideal_carry and not self.demo_motion:
            if getattr(operation,'articulation_preview',None) is None:
                from .local_articulation import prepare_articulation
                operation.articulation_preview=prepare_articulation(self,obj,primitive,operation)
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
                if primitive in {'open','close'} and self.ideal_carry and not self.demo_motion:
                    if not self._admit_articulation(primitive,obj,pose,operation,budget.check):
                        row.update(status='rejected',reason='articulation_clearance');continue
                if preview is not None:
                    if not self._admit_loaded_departure(pose,preview,operation,budget.check):
                        row.update(status='rejected',reason='no_loaded_departure',
                                   diagnostic=getattr(operation,'preview_failure',None));continue
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
        operation.articulation_preview=None
        operation.articulation_plan=None

    def _local_clearance_recovery(self,operation,geometry):
        from dataclasses import replace
        from .gt_navigation import plan_start_clearance_exit,NavigationError,PlanningBudget
        from .live_gt_map import clearance_transition_guard
        grid,_,details,_,_=geometry;actual=self._local_pose()
        raw,overlap,radius=self._navigation_clearance
        budget=PlanningBudget(operation.check,seconds=6.)
        guard=clearance_transition_guard(grid,self._navigation_layers,self._navigation_support,
            [(layer['z_min'],layer['z_max']) for layer in details['collision_layers']]
            if 'collision_layers' in details else None)
        def allowed(a,b):
            return (math.dist(grid.world(b),operation.origin[:2])<=operation.limits.radius_m and guard(a,b))
        try:
            corridor,plan,report=plan_start_clearance_exit(grid,raw,overlap,actual[:2],
                radius=min(radius,.75),transition_allowed=allowed,check_cancelled=budget.check)
        except NavigationError as exc:
            raise SkillError('local_adjustment_blocked','No guarded local clearance recovery: '+str(exc)) from exc
        if plan.geodesic_m+operation.path>operation.limits.path_m:
            raise SkillError('local_adjustment_exhausted','Clearance recovery exceeds local path budget')
        plan=replace(plan,travel_yaw=actual[2],final_yaw=actual[2],motion_mode='clearance_retreat')
        operation.record('clearance_recovery',plan=asdict(plan),**report)
        result=self._execute_gt_plan(corridor,plan,operation.step_limit-(self.steps-operation.start_steps))
        self.navigation_distance+=result['actual_path_distance_m']
        operation.geometry=None;operation.grasp_preview=None
        operation.articulation_preview=None;operation.articulation_plan=None

    def _admit_articulation(self,primitive,obj,pose,operation,check):
        from .local_articulation import prepare_articulation
        from .forward_navigation import local_exit_certificate
        preview=getattr(operation,'articulation_preview',None)
        if preview is None:
            preview=prepare_articulation(self,obj,primitive,operation)
            operation.articulation_preview=preview
        swept,future,plan=preview
        swept.check_cancelled=future.check_cancelled=check
        if not swept.motion_free(pose,pose):return False
        if local_exit_certificate(pose,future,check_cancelled=check) is None:return False
        operation.articulation_plan=plan
        return True

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
            if certificate is not None:return 'checked'
            if operation.repositions>=operation.limits.max_repositions:return 'blocked'
            if not geometry[1].motion_free(self._local_pose(),self._local_pose()):
                self._local_clearance_recovery(operation,geometry)
                operation.repositions+=1
                geometry=self._local_geometry(operation)
                if local_exit_certificate(self._local_pose(),geometry[1],check_cancelled=operation.check) is not None:
                    operation.record('departure_recovered',kind='clearance_retreat')
                    return 'checked'
                if operation.repositions>=operation.limits.max_repositions:return 'blocked'
            # A free pose may still lack a short exit certificate. Try a
            # checked forward route to a nearby pose with a certified exit.
            from .gt_navigation import NavigationPlan,NavigationError,PlanningBudget
            from .forward_navigation import plan_forward_route
            from .local_manipulation import LocalCollision
            actual=self._local_pose();grid,collision,_,_,_=geometry
            budget=PlanningBudget(operation.check,seconds=6.)
            collision.check_cancelled=budget.check
            bounded=LocalCollision(collision,operation.origin,operation.limits.radius_m)
            try:
                for pose in stance_candidates(actual,operation.origin,(*actual[:2],0.),operation.limits):
                    budget.check()
                    if math.dist(actual[:2],pose[:2])>.75:continue
                    if not bounded.motion_free(pose,pose):continue
                    if local_exit_certificate(pose,collision,check_cancelled=budget.check) is None:continue
                    plan=NavigationPlan((actual[:2],pose[:2]),pose[:2],pose[:2],pose[2],
                        math.dist(actual[:2],pose[:2]),0.,1,1,0.,0,pose[2],'forward')
                    try:route=plan_forward_route(plan,actual,bounded,check_cancelled=budget.check,max_expansions=2000)
                    except NavigationError:continue
                    if operation.path+route.distance_m>operation.limits.path_m:continue
                    operation.record('departure_recovery',route=asdict(route))
                    result=self._execute_gt_plan(grid,plan,operation.step_limit-(self.steps-operation.start_steps),prepared_route=route)
                    self.navigation_distance+=result['actual_path_distance_m'];operation.repositions+=1
                    fresh=self._local_geometry(operation)
                    return ('checked' if local_exit_certificate(self._local_pose(),fresh[1],check_cancelled=operation.check)
                            is not None else 'blocked')
            except NavigationError:return 'blocked'
            finally:collision.check_cancelled=operation.check
            return 'blocked'
        except SkillError as exc:
            if exc.code in {'episode_cancelled','episode_timeout'}:return 'not_checked_cancelled'
            return 'not_checked_budget' if exc.code=='local_adjustment_exhausted' else 'blocked'

    def _execute_local_manipulation(self,primitive,obj,point,grounding,max_steps,options):
        binding=LockedTarget.bind(obj,point,grounding)
        operation=LocalOperation(self,primitive,binding,max_steps,options)
        self._local_operation=operation
        try:return operation.run()
        finally:self._local_operation=None
