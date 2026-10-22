"""Coordination contracts with a deterministic motor, not task-success evidence."""
from dataclasses import replace
from types import SimpleNamespace
import math
import unittest
from unittest.mock import patch
import tempfile
from pathlib import Path
from manipulation_agent.contracts import Budget, SkillError
from manipulation_agent.executors.local_manipulation import LocalLimits, LocalOperation, stance_candidates
from manipulation_agent.executors.visual_target import LockedTarget


class Motor:
    def __init__(self):
        self.local_limits=LocalLimits()
        self.steps=self.sampling_physics_steps=0
        self.pose=(0.,0.,0.)
        self.point=(1.6,0.,.7)
        self.target=object()
        self.events=[];self.executions=[];self.refreshes=0
        self.deadline=SimpleNamespace(check=lambda **kw:None)
        self.binding=SimpleNamespace(grounding={'private':'selection'},describe=lambda:{'private':'target'})
        self.operation_error=None
        self.precondition_error=None
    def _local_pose(self):return self.pose
    def _local_record(self,**kw):self.events.append(kw)
    def _resolve_local_target(self,binding):
        assert binding is self.binding
        return self.target,self.point
    def _manipulation_preconditions(self,*args):
        if self.precondition_error:raise SkillError(self.precondition_error,'precondition')
    def _local_ready(self,primitive,obj,point,op):return math.dist(self.pose[:2],point[:2])<=1.4
    def _local_reposition(self,primitive,obj,point,op):
        op.admit_pose((self.pose[0]+.3,0.,0.))
        self.pose=(self.pose[0]+.3,0.,0.);self.steps+=1
    def _local_reobserve(self,binding):self.refreshes+=1
    def _execute_bound_manipulation(self,primitive,obj,point,grounding,max_steps,**kw):
        self.executions.append((obj,point,kw))
        if self.operation_error:raise SkillError(self.operation_error,'effect failure')
        self.steps+=1
        return {'postcondition':'checked'}
    def _local_departure(self,*args):return 'checked'
    def run(self,**kwargs):
        return LocalOperation(self,'grasp',self.binding,100,kwargs).run()


class CoordinationTests(unittest.TestCase):
    def test_harness_one_revision_and_cached_request_after_internal_motion(self):
        from manipulation_agent.observations.mock_rgb import MockRGBBackend
        from manipulation_agent.records import Recorder
        from manipulation_agent.vision_harness import VisionHarness
        with tempfile.TemporaryDirectory() as directory:
            recorder=Recorder(Path(directory)/'run',{'validation':'cpu_fixture'})
            b=MockRGBBackend(recorder.output);b.local_manipulation=True
            motor=Motor();parents=[]
            def execute(*args,**kwargs):
                parents.append(b._active_request_id)
                result=motor.run();b.steps=motor.steps
                return {**result,'private_grounding':{'object_id':'do_not_publish'}}
            b.execute_visual=execute
            h=VisionHarness(b,recorder,Budget(wall_seconds=None),profile='minimal',agent_step_limit=50)
            self.assertEqual(h.initialize()['control']['manipulation_protocol'],'target_locked_approach_v2')
            args={'primitive':'grasp','revision':0,'target':{
                'image_ref':h.snapshot['images'][0]['image_ref'],'point':[.5,.5]}}
            result=h.call('act',args,'one-brain-action')
            self.assertEqual(result,h.call('act',args,'one-brain-action'))
            self.assertEqual(parents,['one-brain-action'])
            self.assertEqual(h.revision,1);self.assertEqual(h.agent_steps,1)
            self.assertEqual(len(motor.executions),1)
            self.assertEqual(result['effect']['local_adjustment']['repositions'],1)
            self.assertNotIn('do_not_publish',str(result))
            self.assertNotIn('path_m',str(result))

    def test_reach_is_resolved_inside_one_operation_on_same_target(self):
        b=Motor();r=b.run()
        self.assertEqual(len(b.executions),1)
        self.assertIs(b.executions[0][0],b.target)
        self.assertEqual(b.refreshes,1)
        self.assertEqual(r['local_manipulation']['repositions'],1)
        self.assertEqual(r['local_manipulation']['primitive_attempts'],1)
    def test_ready_target_executes_without_unnecessary_motion(self):
        b=Motor();b.point=(1.,0.,.7);r=b.run()
        self.assertEqual(r['local_manipulation']['repositions'],0)
    def test_far_selected_target_can_approach_and_execute_in_one_call(self):
        b=Motor();b.point=(6.,0.,.7)
        def approach(primitive,obj,point,op):
            pose=stance_candidates(b.pose,op.origin,point,op.limits)[0]
            op.admit_pose(pose);b.pose=pose;b.steps+=1
        b._local_reposition=approach
        result=b.run()['local_manipulation']
        self.assertGreater(result['path_m'],4.)
        self.assertEqual(len(b.executions),1)
        self.assertIs(b.executions[0][0],b.target)
    def test_semantic_failure_precedes_any_reposition(self):
        b=Motor();b.precondition_error='container_closed'
        with self.assertRaises(SkillError) as cm:b.run()
        self.assertEqual(cm.exception.code,'container_closed');self.assertEqual(b.steps,0)
    def test_sampling_failure_is_not_retried_or_disguised_as_navigation(self):
        b=Motor();b.operation_error='sampling_error'
        with self.assertRaises(SkillError) as cm:b.run()
        self.assertEqual(cm.exception.code,'sampling_error');self.assertTrue(cm.exception.changed)
        self.assertEqual(len(b.executions),1)
    def test_zero_motion_stops_without_loop(self):
        b=Motor();b._local_reposition=lambda *args:None
        with self.assertRaises(SkillError) as cm:b.run()
        self.assertEqual(cm.exception.code,'local_adjustment_blocked');self.assertEqual(b.executions,[])
    def test_loaded_departure_search_timeout_returns_bounded_failure(self):
        from manipulation_agent.executors.gt_navigation import GridMap, SearchBudgetExhausted
        from manipulation_agent.executors.local_rgb import LocalRGBManipulation
        b=Motor();b.ideal_carry=True;b.demo_motion=False
        grid=GridMap(41,41,.1,(-2.,-2.),bytes([1])*41**2)
        collision=SimpleNamespace(grid=grid,motion_free=lambda *args:True)
        b._local_geometry=lambda operation:(grid,collision,{'held_object_footprint_included':False},[],0)
        b._approach_visible=lambda *args,**kwargs:True
        b._local_grasp_preview=lambda *args:lambda pose:collision
        b._local_reposition=LocalRGBManipulation._local_reposition.__get__(b)
        b._admit_loaded_departure=LocalRGBManipulation._admit_loaded_departure.__get__(b)
        # This fixture isolates budget propagation without a live map to refine.
        b._refine_local_geometry=lambda *args,**kwargs:False
        # The incoming route covers the unloaded stance; the loaded departure
        # search must still propagate exhaustion before executing a grasp.
        with patch('manipulation_agent.executors.forward_navigation.local_exit_certificate',
                   side_effect=SearchBudgetExhausted()):
            with self.assertRaises(SkillError) as caught:b.run()
        self.assertEqual(caught.exception.code,'local_adjustment_exhausted')
        self.assertFalse(caught.exception.changed)
        self.assertEqual(b.steps,0);self.assertEqual(b.executions,[])
        search=next(e for e in b.events if e['phase']=='stance_search')
        self.assertTrue(search['candidate_preparation_exhausted'])
        self.assertEqual(search['termination'],'budget')
        self.assertEqual(search['route_candidates'],0)
        candidates=next(e for e in b.events if e['phase']=='candidates')
        self.assertEqual(len(candidates['candidates']),1)
        self.assertEqual(search['attempts'],0)
        self.assertEqual(caught.exception.stage,'local_candidate_preparation_budget')
        self.assertEqual(b.events[-1]['phase'],'failed')
        self.assertEqual(b.events[-1]['error_code'],'local_adjustment_exhausted')
    def test_motion_accounting_has_no_start_radius_or_distance_cap(self):
        b=Motor();op=LocalOperation(b,'grasp',b.binding,100,{})
        for x in (3.,0.,5.):op.admit_pose((x,0.,0.))
        self.assertAlmostEqual(op.path,11.)
        b.steps=100
        with self.assertRaises(SkillError):op.admit_pose((6.,0.,0.))
        self.assertAlmostEqual(op.path,11.)
    def test_outer_local_target_can_execute_with_one_internal_reposition(self):
        b=Motor();b.point=(2.8,0.,.7)
        def reposition(primitive,obj,point,op):
            pose=stance_candidates(b.pose,op.origin,point,op.limits)[0]
            op.admit_pose(pose);b.pose=pose;b.steps+=1
        b._local_reposition=reposition
        result=b.run()['local_manipulation']
        self.assertEqual(result['repositions'],1)
        self.assertGreater(result['path_m'],1.)
        self.assertLessEqual(result['path_m'],2.)
        self.assertEqual(len(b.executions),1)
        self.assertIs(b.executions[0][0],b.target)

    def test_allowed_reach_is_not_clipped_to_preferred_distance(self):
        b=Motor();b.point=(3.3,0.,1.)
        def reposition(primitive,obj,point,op):
            candidates=stance_candidates(b.pose,op.origin,point,op.limits)
            self.assertTrue(candidates)
            pose=candidates[0];op.admit_pose(pose);b.pose=pose;b.steps+=1
        b._local_reposition=reposition
        result=b.run()['local_manipulation']
        self.assertEqual(result['primitive_attempts'],1)
        self.assertLessEqual(math.dist(b.pose[:2],b.point[:2]),1.4)
        self.assertGreater(math.dist(b.pose[:2],b.point[:2]),1.25)

    def test_candidate_prefix_covers_outer_disk_and_both_sides(self):
        candidates=stance_candidates((0.,0.,0.),(0.,0.,0.),(1.3,0.,1.),LocalLimits())
        prefix=candidates[:12]
        self.assertTrue(any(math.hypot(*p[:2])>1. for p in prefix))
        self.assertTrue(any(p[1]>.1 for p in prefix))
        self.assertTrue(any(p[1]<-.1 for p in prefix))
    def test_candidates_cover_target_disk_beyond_original_action_center(self):
        origin=(0.,0.,0.);actual=(1.5,0.,0.);point=(3.,0.,.7)
        candidates=stance_candidates(actual,origin,point,LocalLimits())
        self.assertTrue(candidates)
        self.assertTrue(any(math.dist(p[:2],origin[:2])>2. for p in candidates))
        self.assertTrue(all(math.dist(p[:2],point[:2])<=1.4 for p in candidates))
    def test_explicit_collision_window_restricts_only_its_own_users(self):
        from manipulation_agent.executors.gt_navigation import GridMap
        from manipulation_agent.executors.local_manipulation import LocalCollision
        grid=GridMap(61,61,.1,(-3.,-3.),bytes([1])*61**2)
        collision=SimpleNamespace(grid=grid,motion_free=lambda *args:True,optimistic_grid=lambda:grid)
        local=LocalCollision(collision,(0.,0.,0.),2.)
        for restricted in (local.grid,local.optimistic_grid()):
            self.assertTrue(restricted.navigable(restricted.cell((1.8,0.))))
            self.assertFalse(restricted.navigable(restricted.cell((2.1,0.))))
        self.assertTrue(local.motion_free((0.,0.,0.),(1.8,0.,0.)))
        self.assertFalse(local.motion_free((1.8,0.,0.),(2.01,0.,0.)))
    def test_certified_route_reserves_control_steps_and_preserves_atomic_allowance(self):
        b=Motor();clock=[0.];op=LocalOperation(b,'grasp',b.binding,20000,{},clock=lambda:clock[0])
        # Recorded failed approach: 35.49 simulated seconds exceeded 600 steps.
        route=SimpleNamespace(estimated_motion_seconds=35.4866,phases=(None,)*50)
        motion=op.reserve_route(route,1/30)
        self.assertGreater(motion,600)
        self.assertEqual(op.step_limit,600+motion)
        # Slow rendering may exceed the non-motion wall allowance; progress
        # remains bounded by reserved steps and the follower's stall checks.
        clock[0]=350.;b.steps=motion-1;op.check()
        op.finish_route();clock[0]=651.
        with self.assertRaises(SkillError):op.check()

    def test_insufficient_caller_budget_is_rejected_before_moving(self):
        b=Motor();op=LocalOperation(b,'grasp',b.binding,700,{})
        route=SimpleNamespace(estimated_motion_seconds=35.4866,phases=(None,)*50)
        with self.assertRaises(SkillError) as caught:op.reserve_route(route,1/30)
        self.assertEqual(caught.exception.stage,'local_control_budget')
        self.assertFalse(caught.exception.changed)
        self.assertEqual(b.steps,0);self.assertEqual(op.step_limit,600)

    def test_route_allowance_still_honors_caller_limit_and_cancellation(self):
        b=Motor();op=LocalOperation(b,'grasp',b.binding,1000,{})
        route=SimpleNamespace(estimated_motion_seconds=15.,phases=(None,)*10)
        op.reserve_route(route,1/30);self.assertEqual(op.step_limit,1000)
        b.steps=1000
        with self.assertRaises(SkillError):op.check()

    def test_time_steps_and_sampling_are_bounded(self):
        for cause in ('time','steps','sampling'):
            b=Motor();clock=[0.];op=LocalOperation(b,'grasp',b.binding,100,{},clock=lambda:clock[0])
            if cause=='time':clock[0]=301.
            elif cause=='steps':b.steps=100
            else:b.sampling_physics_steps=1200
            with self.subTest(cause=cause),self.assertRaises(SkillError) as cm:op.check()
            self.assertEqual(cm.exception.code,'local_adjustment_exhausted')
    def test_cancellation_precedes_effect(self):
        b=Motor()
        def stop(**kw):raise SkillError('episode_cancelled','stop')
        b.deadline.check=stop
        with self.assertRaises(SkillError) as cm:b.run()
        self.assertEqual(cm.exception.code,'episode_cancelled');self.assertEqual(b.executions,[])
    def test_committed_operation_not_repeated_when_departure_blocked(self):
        b=Motor();b._local_departure=lambda *args:'blocked'
        r=b.run()
        self.assertEqual(r['local_manipulation']['departure'],'blocked');self.assertEqual(len(b.executions),1)

    def test_committed_operation_recovers_clearance_without_repeating_effect(self):
        from manipulation_agent.executors.local_rgb import LocalRGBManipulation
        b=Motor();b.point=(1.,0.,.7)
        free=[False];recoveries=[]
        collision=SimpleNamespace(motion_free=lambda *args:free[0])
        b._local_geometry=lambda op:(None,collision,None,None,None)
        def recovery(op,geometry):
            recoveries.append(op);free[0]=True;b.steps+=1
        b._local_clearance_recovery=recovery
        b._local_departure=LocalRGBManipulation._local_departure.__get__(b)
        with patch('manipulation_agent.executors.forward_navigation.local_exit_certificate',
                   side_effect=[None,{'poses':[]} ]):
            result=b.run()['local_manipulation']
        self.assertEqual(result['departure'],'checked')
        self.assertEqual(result['repositions'],1)
        self.assertEqual(len(recoveries),1);self.assertEqual(len(b.executions),1)
    def test_candidate_search_stays_within_target_reach_and_avoids_old_pose(self):
        limits=LocalLimits();actual=(0.,0.,0.);point=(1.6,0.,1.)
        candidates=stance_candidates(actual,actual,point,limits,[(.3,0.,0.)])
        self.assertTrue(candidates)
        for p in candidates:
            self.assertLessEqual(math.dist(p[:2],point[:2]),limits.reach_m)
        self.assertNotIn((.3,0.,0.),candidates)


class TargetLockTests(unittest.TestCase):
    def test_link_relative_point_tracks_selected_door_and_never_another_object(self):
        pose=[(1.,2.,3.),(0.,0.,0.,1.)]
        link=SimpleNamespace(prim_path='/obj/door',get_position_orientation=lambda:tuple(pose))
        obj=SimpleNamespace(prim_path='/obj',links={'door':link})
        binding=LockedTarget.bind(obj,(1.1,2.,3.),{'visual_mesh':'/obj/door/visuals/mesh'})
        pose[:]=[(1.,2.,3.),(0.,0.,math.sin(math.pi/4),math.cos(math.pi/4))]
        selected,point=binding.resolve([obj],.25)
        self.assertIs(selected,obj);self.assertAlmostEqual(point[0],1.);self.assertAlmostEqual(point[1],2.1)
        lookalike=SimpleNamespace(prim_path='/obj',links={'door':link})
        with self.assertRaises(SkillError):binding.resolve([lookalike],.25)
    def test_large_target_motion_and_replaced_link_abort(self):
        pose=[(0.,0.,0.),(0.,0.,0.,1.)]
        link=SimpleNamespace(prim_path='/obj/link',get_position_orientation=lambda:tuple(pose))
        obj=SimpleNamespace(prim_path='/obj',links={'link':link})
        binding=LockedTarget.bind(obj,(0.,0.,0.),{'visual_mesh':'/obj/link/visuals/mesh'})
        pose[0]=(1.,0.,0.)
        with self.assertRaises(SkillError):binding.resolve([obj],.25)
        obj.links={}
        with self.assertRaises(SkillError):binding.resolve([obj],2.)


if __name__=='__main__':unittest.main()
