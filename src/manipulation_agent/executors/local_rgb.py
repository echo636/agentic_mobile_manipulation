"""OmniGibson adapter for local manipulation; GT stays executor-private."""
from dataclasses import asdict
import json
import logging
import math
from ..contracts import SkillError
from ..records import now
from .local_manipulation import LocalLimits, LocalOperation, stance_candidates
from .visual_target import LockedTarget


# These effects act on the selected object as a whole. Its visible collision
# surface is a valid approach reference even when it differs from the rendered
# click point. Surface-specific placements and attachment points remain strict.
OBJECT_REFERENCE_ACTIONS = frozenset({
    'grasp', 'open', 'close', 'toggle_on', 'toggle_off', 'cut', 'soak',
    'wipe', 'sweep', 'vacuum', 'spray', 'spread', 'pour', 'scoop',
    'place_inside', 'place_next_to', 'place_under',
})


class LocalRGBManipulation:
    def _local_pose(self):
        from .visual_target import rotate
        pos,quat=self.robot.get_position_orientation()
        forward=rotate((1.,0.,0.),quat)
        return float(pos[0]),float(pos[1]),math.atan2(forward[1],forward[0])

    def _local_record(self, **data):
        if getattr(self, '_local_trace_unavailable', False):return
        try:
            with (self.output/'local_manipulation.jsonl').open('a') as stream:
                stream.write(json.dumps({'at':now(),'parent_request_id':getattr(self,'_active_request_id',None),
                                        'env_step':self.steps,'audience':'executor_private',**data})+'\n')
        except OSError as exc:
            # Optional tracing must not abort an action after physical motion.
            # Preserve the failure in the process log; core run records stay strict.
            self._local_trace_unavailable = True
            logging.getLogger(__name__).warning('Local manipulation trace disabled at %s: %s', self.output, exc)

    def _resolve_local_target(self,binding):
        obj,point=binding.resolve(self.env.scene.objects,self.local_limits.max_target_drift_m)
        base=self.robot.get_position_orientation()[0]
        return obj,self.torch.tensor(point,dtype=base.dtype,device=base.device)

    def _local_geometry(self,operation):
        from .navigation.live_gt_map import build_navigation_grid
        from .navigation.heading_collision import HeadingCollisionChecker
        position=self.robot.get_position_orientation()[0]
        trav=self.env.scene.trav_map
        floor=min(range(len(trav.floor_heights)),key=lambda i:abs(float(position[2])-trav.floor_heights[i]))
        grid,details=build_navigation_grid(self,floor)
        self._navigation_layer_yaw=self._local_pose()[2]
        collision=HeadingCollisionChecker(grid,self._navigation_layers,self._navigation_layer_yaw,
                                          self._navigation_support,check_cancelled=operation.check)
        # Navigation's earlier map may differ after transport or settling.
        # Keep the actual operation map so failed approach searches can be
        # replayed without claiming that a stale navigation map was exact.
        import numpy as np
        import hashlib
        self._local_grid_index=getattr(self,'_local_grid_index',0)+1
        folder=self.output/'local_grids';folder.mkdir(exist_ok=True)
        path=folder/f'{self._local_grid_index:04d}.npz'
        np.savez_compressed(path,
            free=np.frombuffer(grid.free,dtype='uint8').reshape(grid.height,grid.width),
            origin=grid.origin,resolution=grid.resolution,start=self._local_pose(),
            raw_layers=np.stack([layer[0] for layer in collision.layers]),
            support=self._navigation_support,initial_layer_yaw=collision.reference_yaw,
            layer_footprints_json=json.dumps([{'footprint':layer[1].tolist()} for layer in collision.layers]))
        details['grid_artifact']=str(path.relative_to(self.output))
        operation.record('geometry',grid_artifact=details['grid_artifact'],
                         grid_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                         start=self._local_pose(),floor=floor)
        hulls,_=self._navigation_self_hulls()
        return grid,collision,details,hulls,floor

    def _local_ready(self,primitive,obj,point,operation):
        pose=self._local_pose()
        if math.dist(pose[:2],point[:2])>operation.limits.reach_m:return False
        if self._get_held() is obj and primitive in {'grasp','toggle_on','toggle_off'}:return True
        geometry=self._local_geometry(operation)
        operation.geometry=geometry
        if not geometry[1].motion_free(pose,pose):return False
        if not self._approach_visible(pose[:2],point,obj,margin=.04,self_hulls=geometry[3],base_yaw=pose[2],
                                      selected_container=primitive in OBJECT_REFERENCE_ACTIONS,
                                      tracked_target=True):return False
        return True

    def _local_reposition(self,primitive,obj,point,operation):
        from collections import Counter
        from .navigation.gt_navigation import NavigationPlan, NavigationError, PlanningBudget, SearchBudgetExhausted
        from .navigation.forward_navigation import (plan_forward_route,
                                         checked_connection, route_from_poses, angle)
        from .local_stances import local_stance_proposals
        geometry=getattr(operation,'geometry',None) or self._local_geometry(operation)
        grid,collision,details,hulls,_=geometry
        actual=self._local_pose()
        if not collision.motion_free(actual,actual):
            if self._refine_local_geometry(operation,point,grid,reason='coarse_start_collision'):
                return self._local_reposition(primitive,obj,point,operation)
            self._local_clearance_recovery(operation,geometry)
            return
        # Endpoint checks only need the target neighbourhood plus the full
        # short departure (at most .7m). Routes retain the full-floor checker.
        local=(collision.local_window(point[:2],operation.limits.reach_m+.75)
               if hasattr(collision,'local_window') else collision)
        budget=PlanningBudget(operation.check,seconds=operation.limits.planning_seconds)
        collision.check_cancelled=budget.check
        selected=None;attempts=[];exhausted=False;proposal_diagnostics={};eligible=[]
        # A short forward departure can lead deeper into a blind pocket.
        # Surface work keeps the rigid load/obstacles unchanged, so prefer
        # stances with a swept-checked return to the measured approach pose.
        # This ranks proposals only: legitimate one-way approaches remain
        # available when no return connector is certified within this budget.
        prefer_return=primitive in {'wipe','sweep','vacuum','spray','soak','spread','pour','scoop'}
        preparation=PlanningBudget(budget.check,seconds=4.)
        collision.check_cancelled=preparation.check
        try:
            try:
                proposals=local_stance_proposals(actual,operation.origin,point,operation.limits,local,
                    operation.visited,check_cancelled=preparation.check,diagnostics=proposal_diagnostics)
                for pose,source in proposals:
                    preparation.check()
                    row={'pose':pose,'source':source,'status':'checking'};attempts.append(row)
                    if not local.motion_free(pose,pose):row.update(status='rejected',reason='collision');continue
                    visibility={}
                    if not self._approach_visible(pose[:2],point,obj,margin=.04,self_hulls=hulls,
                                                  base_yaw=pose[2],diagnostics=visibility,
                                                  selected_container=primitive in OBJECT_REFERENCE_ACTIONS,
                                                  tracked_target=True):
                        row.update(status='rejected',reason='target_not_visible',visibility=visibility);continue
                    plan=NavigationPlan((actual[:2],pose[:2]),pose[:2],tuple(map(float,point[:2])),pose[2],
                        math.dist(actual[:2],pose[:2]),math.dist(pose[:2],point[:2]),len(attempts),1,0.,0,pose[2],'forward')
                    eligible.append((plan,row))
                    row['status']='route_candidate'
                    if prefer_return:
                        row['checked_return_poses']=checked_connection(
                            pose,actual[:2],actual[2],collision,preparation.check)
                    poses=checked_connection(actual,pose[:2],pose[2],collision,preparation.check)
                    if poses is not None and (not prefer_return or row['checked_return_poses'] is not None):
                        selected=(plan,route_from_poses(plan,poses,collision,
                            method='certified_target_approach',check_cancelled=preparation.check))
                        break
                    if len(eligible)>=32:
                        proposal_diagnostics['candidate_pool_limited']=True
                        exhausted=True
                        break
            except SearchBudgetExhausted:
                proposal_diagnostics['candidate_preparation_exhausted']=True
                exhausted=True
            collision.check_cancelled=budget.check
            if selected is None and eligible:
                returning=[entry for entry in eligible if entry[1].get('checked_return_poses') is not None]
                preferred=returning if prefer_return and returning else eligible
                plans=[entry[0] for entry in preferred]
                proposal_diagnostics['checked_return_candidates']=len(returning)
                try:
                    # Reuse the compiled shared roadmap as a fast proposal;
                    # continuous search remains the fallback for admitted stances.
                    from .navigation.point_route_projection import project_forward_point
                    roadmap_budget=PlanningBudget(budget.check,seconds=3.)
                    collision.check_cancelled=roadmap_budget.check
                    try:
                        roadmap=project_forward_point(plans,actual,collision,roadmap_budget.check,
                                                      exact_endpoints=True)
                    except (ImportError,SearchBudgetExhausted):roadmap=None
                    finally:collision.check_cancelled=budget.check
                    if roadmap is not None:
                        plan,poses,roadmap_details=roadmap
                        proposal_diagnostics['roadmap']=roadmap_details
                        route=route_from_poses(plan,poses,collision,method='shared_roadmap_to_admitted_stances',
                                               check_cancelled=budget.check)
                    else:
                        route=plan_forward_route(plans[0],actual,collision,check_cancelled=budget.check,
                                                 max_expansions=20000,alternatives=plans[1:])
                        end=route.phases[-1]
                        plan=next(p for p in plans if math.dist(p.goal,end.goal)<1e-6
                                  and abs(angle(p.final_yaw-end.final_yaw))<1e-6)
                    selected=(plan,route)
                except NavigationError as exc:
                    exhausted|=exc.code=='navigation_search_exhausted'
                    for _,row in eligible:
                        row.update(status='rejected',reason=exc.code,detail=str(exc))
            # R1Pro is holonomic: a narrow aisle may require a sideways drive
            # while its long axis stays fixed. Preserve all endpoint proofs and
            # use the same footprint sweeps; differential bases never enter here.
            if selected is None and eligible and getattr(self.robot,'is_holonomic_base',False):
                from .navigation.point_route_projection import project_forward_point
                lateral_budget=PlanningBudget(operation.check,seconds=min(40.,12.*.05/grid.resolution))
                collision.check_cancelled=lateral_budget.check
                try:
                    found=project_forward_point([entry[0] for entry in eligible],actual,collision,
                        lateral_budget.check,exact_endpoints=True,allow_lateral=True)
                    if found is not None:
                        plan,poses,route_details=found
                        route=route_from_poses(plan,poses,collision,allow_lateral=True,
                            method='checked_holonomic_target_approach',check_cancelled=lateral_budget.check)
                        selected=(plan,route)
                        proposal_diagnostics['holonomic_roadmap']=route_details
                except (ImportError,SearchBudgetExhausted):
                    exhausted=True
                    proposal_diagnostics['holonomic_planning_exhausted']=True
                finally:
                    collision.check_cancelled=operation.check
            if selected is not None:
                for plan,row in eligible:
                    if plan is selected[0]:
                        row['status']='selected'
                        row.pop('reason',None);row.pop('detail',None)
                    elif row['status']=='route_candidate':row['status']='not_selected'
        except SearchBudgetExhausted:
            exhausted=True
            operation.record('planning_limit',candidate_attempts=len(attempts))
        finally:
            collision.check_cancelled=operation.check
            proposal_diagnostics.update(route_search='shared_target_stances',route_candidates=len(eligible))
            operation.record('candidates',candidates=attempts,held_footprint=details['held_object_footprint_included'])
            operation.record('stance_search',**proposal_diagnostics,
                candidate_sources=dict(Counter(row['source'] for row in attempts)),
                candidate_outcomes=dict(Counter(row.get('reason',row['status']) for row in attempts)),
                termination='selected' if selected else 'budget' if exhausted else 'candidates_exhausted')
        if selected is None:
            if self._refine_local_geometry(operation,point,grid,
                    reason='coarse_planning_budget' if exhausted else 'coarse_candidates_exhausted'):
                return self._local_reposition(primitive,obj,point,operation)
            if exhausted:
                if not eligible:
                    raise SkillError('local_adjustment_exhausted',
                        'Stance preparation exhausted its budget before admitting a route endpoint',
                        stage='local_candidate_preparation_budget')
                raise SkillError('local_adjustment_exhausted','Local route search incomplete within planning budget',
                                 stage='local_planning_budget')
            reasons={row.get('reason') for row in attempts}
            stage='local_stance_collision'
            for reason,value in [('target_not_visible','local_target_visibility'),
                                 ('navigation_unreachable','local_route')]:
                if reason in reasons:stage=value
            raise SkillError('local_adjustment_blocked','No checked reachable visible stance within target operation reach',
                             stage=stage)
        plan,route=selected
        from .navigation.navigation_refinement import refine_navigation_detour
        grid,details,plan,route,collision,_,detour=refine_navigation_detour(
            self,geometry[4],grid,details,actual,[plan],plan,route,collision,{})
        if detour is not None:
            # Refinement has its own attempt list; LocalOperation owns the
            # top-level primitive-attempt counter in every diagnostic record.
            operation.record('detour_refinement',refinement=detour)
            if detour['status']=='refined_route_selected':
                chosen=detour['attempts'][-1]
                operation.navigation_sampling={key:chosen[key] for key in ('resolution','window')}
        operation.record('move',route=asdict(route))
        # Refinement can certify the existing pose; no motion needs settling.
        if route.distance_m == 0 and route.turn_radians == 0:
            return
        result=self._execute_gt_plan(grid,plan,operation.step_limit-(self.steps-operation.start_steps),prepared_route=route)
        self.navigation_distance+=result['actual_path_distance_m']
        # State and geometry must be measured again after physics, even if a
        # route was certified before executing it.
        operation.geometry=None

    def _refine_local_geometry(self,operation,point,grid,*,reason):
        """Refine target geometry within the full-window cell budget.

        Coarse cells include full occupied columns and an outward footprint
        reserve. A physically open passage can disappear in that raster. Requery
        a bounded local map; never remove obstacles or shrink robot geometry.
        """
        if grid.resolution<=.0125:return False
        desired=max(.0125,grid.resolution/2)
        actual=self._local_pose()
        margin=operation.limits.reach_m+.7
        window=[min(actual[0],float(point[0]))-margin,
                min(actual[1],float(point[1]))-margin,
                max(actual[0],float(point[0]))+margin,
                max(actual[1],float(point[1]))+margin]
        def cells(resolution):
            return math.prod(math.ceil((window[i+2]-window[i])/resolution)+2 for i in (0,1))
        resolution=desired
        if cells(resolution)>250000:
            # A requested halving can exceed the budget while a slightly
            # coarser refinement still resolves the passage. Keep both the
            # full approach window and the cell cap; choose its finest fit.
            if cells(grid.resolution)>250000:
                resolution=None
            else:
                low,high=desired,grid.resolution
                for _ in range(40):
                    mid=(low+high)/2
                    if cells(mid)>250000:low=mid
                    else:high=mid
                resolution=high if high<grid.resolution-1e-6 else None
            if resolution is None:
                operation.record('map_refinement_skipped',reason='window_budget_saturated',
                                 current_resolution_m=grid.resolution,world_window=window)
                return False
        operation.check()
        operation.navigation_sampling={'resolution':resolution,'window':window}
        operation.geometry=None
        operation.record('map_refinement',reason=reason,coarse_resolution_m=grid.resolution,
                         resolution_m=resolution,requested_resolution_m=desired,
                         cells=cells(resolution),world_window=window,
                         unchanged_checks=['collision_geometry','target_identity','reach'])
        return True

    def _local_clearance_recovery(self,operation,geometry):
        from dataclasses import replace
        from .navigation.gt_navigation import plan_start_clearance_exit,NavigationError,PlanningBudget
        from .navigation.live_gt_map import clearance_transition_guard
        grid,_,details,_,_=geometry;actual=self._local_pose()
        raw,overlap,radius=self._navigation_clearance
        budget=PlanningBudget(operation.check,seconds=6.)
        guard=clearance_transition_guard(grid,self._navigation_layers,self._navigation_support,
            [(layer['z_min'],layer['z_max']) for layer in details['collision_layers']]
            if 'collision_layers' in details else None)
        try:
            corridor,plan,report=plan_start_clearance_exit(grid,raw,overlap,actual[:2],
                radius=min(radius,.75),transition_allowed=guard,check_cancelled=budget.check)
        except NavigationError as exc:
            raise SkillError('local_adjustment_blocked','No guarded local clearance recovery: '+str(exc)) from exc
        plan=replace(plan,travel_yaw=actual[2],final_yaw=actual[2],motion_mode='clearance_retreat')
        operation.record('clearance_recovery',plan=asdict(plan),**report)
        result=self._execute_gt_plan(corridor,plan,operation.step_limit-(self.steps-operation.start_steps))
        self.navigation_distance+=result['actual_path_distance_m']
        operation.geometry=None

    def _local_reobserve(self,binding):
        self._render_rgb_views(require_calibration=True)
        self._resolve_local_target(binding)

    def _local_departure(self,operation,obj):
        from .navigation.forward_navigation import local_exit_certificate
        # A committed operation may open a door or change carried geometry.
        # Check actual post-state but do not repeat/undo its successful effect.
        try:
            operation.check()
            geometry=self._local_geometry(operation)
            certificate=local_exit_certificate(self._local_pose(),geometry[1],check_cancelled=operation.check,allow_lateral=getattr(self.robot,'is_holonomic_base',False))
            if certificate is not None:return 'checked'
            if operation.repositions>=operation.limits.max_repositions:return 'blocked'
            if not geometry[1].motion_free(self._local_pose(),self._local_pose()):
                self._local_clearance_recovery(operation,geometry)
                operation.repositions+=1
                geometry=self._local_geometry(operation)
                if local_exit_certificate(self._local_pose(),geometry[1],check_cancelled=operation.check,allow_lateral=getattr(self.robot,'is_holonomic_base',False)) is not None:
                    operation.record('departure_recovered',kind='clearance_retreat')
                    return 'checked'
                if operation.repositions>=operation.limits.max_repositions:return 'blocked'
            # A free pose may still lack a short exit certificate. Try a
            # checked forward route to a nearby pose with a certified exit.
            from .navigation.gt_navigation import NavigationPlan,NavigationError,PlanningBudget
            from .navigation.forward_navigation import plan_forward_route
            actual=self._local_pose();grid,collision,_,_,_=geometry
            budget=PlanningBudget(operation.check,seconds=6.)
            collision.check_cancelled=budget.check
            try:
                for pose in stance_candidates(actual,operation.origin,(*actual[:2],0.),operation.limits):
                    budget.check()
                    if math.dist(actual[:2],pose[:2])>.75:continue
                    if not collision.motion_free(pose,pose):continue
                    if local_exit_certificate(pose,collision,check_cancelled=budget.check,allow_lateral=getattr(self.robot,'is_holonomic_base',False)) is None:continue
                    plan=NavigationPlan((actual[:2],pose[:2]),pose[:2],pose[:2],pose[2],
                        math.dist(actual[:2],pose[:2]),0.,1,1,0.,0,pose[2],'forward')
                    try:route=plan_forward_route(plan,actual,collision,check_cancelled=budget.check,max_expansions=2000)
                    except NavigationError:continue
                    operation.record('departure_recovery',route=asdict(route))
                    result=self._execute_gt_plan(grid,plan,operation.step_limit-(self.steps-operation.start_steps),prepared_route=route)
                    self.navigation_distance+=result['actual_path_distance_m'];operation.repositions+=1
                    fresh=self._local_geometry(operation)
                    return ('checked' if local_exit_certificate(self._local_pose(),fresh[1],check_cancelled=operation.check,allow_lateral=getattr(self.robot,'is_holonomic_base',False))
                            is not None else 'blocked')
            except NavigationError:return 'blocked'
            finally:collision.check_cancelled=operation.check
            return 'blocked'
        except SkillError as exc:
            if exc.code in {'episode_cancelled','episode_timeout'}:return 'not_checked_cancelled'
            return 'not_checked_budget' if exc.code=='local_adjustment_exhausted' else 'blocked'

    def _execute_local_manipulation(self,primitive,obj,point,grounding,max_steps,options):
        binding=LockedTarget.bind(obj,point,grounding)
        prepared_grasp = primitive == 'grasp' and self._get_held() is None
        if prepared_grasp:
            self._prepare_grasp_arm()
        operation=LocalOperation(self,primitive,binding,max_steps,options)
        operation.changed = prepared_grasp
        self._local_operation=operation
        try:return operation.run()
        finally:self._local_operation=None
