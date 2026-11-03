"""Selected-object identity, one-shot operation and RGB-only feedback contracts."""
from types import SimpleNamespace
import math
import unittest
from unittest.mock import patch
import tempfile
from pathlib import Path
from manipulation_agent.contracts import Budget, SkillError
from manipulation_agent.executors.local_manipulation import LocalLimits, LocalOperation
from manipulation_agent.executors.visual_target import LockedTarget


class Motor:
    def __init__(self):
        self.local_limits=LocalLimits()
        self.steps=self.sampling_physics_steps=0
        self.pose=(0.,0.,0.);self.point=(1.6,0.,.7)
        self.target=object();self.events=[];self.executions=[]
        self.deadline=SimpleNamespace(check=lambda **kw:None)
        self.binding=SimpleNamespace(grounding={'private':'selection'},describe=lambda:{'private':'target'})
        self.operation_error=self.precondition_error=None
    def _local_pose(self):return self.pose
    def _local_record(self,**kw):self.events.append(kw)
    def _resolve_local_target(self,binding):
        assert binding is self.binding
        return self.target,self.point
    def _manipulation_preconditions(self,*args):
        if self.precondition_error:raise SkillError(self.precondition_error,'precondition')
    def _local_ready(self,primitive,obj,point,op):return math.dist(self.pose[:2],point[:2])<=1.4
    def _local_reposition(self,primitive,obj,point,op):
        self.pose=(point[0]-.8,point[1],0.);op.admit_pose(self.pose);self.steps+=1
    def _execute_bound_manipulation(self,primitive,obj,point,grounding,max_steps,**kw):
        self.executions.append((obj,point,kw))
        if self.operation_error:raise SkillError(self.operation_error,'effect failure')
        self.steps+=1
        return {'postcondition':'checked'}
    def run(self,**kwargs):return LocalOperation(self,'grasp',self.binding,100,kwargs).run()


class CoordinationTests(unittest.TestCase):
    def test_same_target_once_and_no_post_operation_departure(self):
        for distance in (1.,1.6,6.):
            with self.subTest(distance=distance):
                b=Motor();b.point=(distance,0.,.7)
                b._local_departure=lambda *a:self.fail('No departure search after the operation')
                result=b.run()['local_manipulation']
                self.assertEqual(result['repositions'],int(distance>1.4))
                self.assertEqual(result['primitive_attempts'],1)
                self.assertIs(b.executions[0][0],b.target)
                self.assertEqual(len(b.executions),1)

    def test_failed_effect_is_not_retried(self):
        for error in ('sampling_error','out_of_reach','postcondition_error'):
            with self.subTest(error=error):
                b=Motor();b.operation_error=error
                with self.assertRaises(SkillError):b.run()
                self.assertEqual(len(b.executions),1)

    def test_semantic_failure_precedes_motion(self):
        b=Motor();b.precondition_error='container_closed'
        with self.assertRaises(SkillError):b.run()
        self.assertEqual(b.steps,0);self.assertFalse(b.executions)

    def test_failed_approach_does_not_repeat_or_apply_remote_effect(self):
        b=Motor();b._local_reposition=lambda *a:None
        with self.assertRaises(SkillError) as caught:b.run()
        self.assertEqual(caught.exception.code,'local_adjustment_blocked')
        self.assertFalse(b.executions)

    def test_trace_io_failure_preserves_action_outcome(self):
        from manipulation_agent.executors.local_rgb import LocalRGBManipulation
        import errno
        b=Motor();b.output=Path('/unavailable-diagnostic-output')
        b._local_record=LocalRGBManipulation._local_record.__get__(b)
        with patch.object(Path,'open',side_effect=OSError(errno.EIO,'Input/output error')) as opened:
            with self.assertLogs('manipulation_agent.executors.local_rgb',level='WARNING'):
                b.run()
        self.assertEqual(opened.call_count,1);self.assertEqual(len(b.executions),1)

    def test_harness_preserves_request_idempotency_and_private_boundary(self):
        from manipulation_agent.observations.mock_rgb import MockRGBBackend
        from manipulation_agent.records import Recorder
        from manipulation_agent.runtime.vision_harness import VisionHarness
        with tempfile.TemporaryDirectory() as directory:
            recorder=Recorder(Path(directory)/'run',{'validation':'cpu_fixture'})
            b=MockRGBBackend(recorder.output);b.local_manipulation=True
            motor=Motor()
            def execute(*args,**kwargs):
                result=motor.run();b.steps=motor.steps
                return {**result,'private_grounding':{'object_id':'do_not_publish'}}
            b.execute_visual=execute
            h=VisionHarness(b,recorder,Budget(wall_seconds=None),profile='minimal',agent_step_limit=50)
            self.assertEqual(h.initialize()['control']['manipulation_protocol'],'target_locked_single_approach_v3')
            args={'primitive':'grasp','revision':0,'target':{
                'image_ref':h.snapshot['images'][0]['image_ref'],'point':[.5,.5]}}
            result=h.call('act',args,'one-brain-action')
            self.assertEqual(result,h.call('act',args,'one-brain-action'))
            self.assertEqual(h.revision,1);self.assertEqual(len(motor.executions),1)
            self.assertNotIn('do_not_publish',str(result));self.assertNotIn('path_m',str(result))
            self.assertNotIn('departure',result['effect']['local_adjustment'])


class TargetLockTests(unittest.TestCase):
    def test_link_relative_point_tracks_selected_door_and_never_another_object(self):
        pose=[(1.,2.,3.),(0.,0.,0.,1.)]
        link=SimpleNamespace(prim_path='/obj/door',get_position_orientation=lambda:tuple(pose))
        obj=SimpleNamespace(prim_path='/obj',links={'door':link})
        binding=LockedTarget.bind(obj,(1.1,2.,3.),{'visual_mesh':'/obj/door/visuals/mesh'})
        pose[:]=[(1.,2.,3.),(0.,0.,math.sin(math.pi/4),math.cos(math.pi/4))]
        selected,point=binding.resolve([obj])
        self.assertIs(selected,obj);self.assertAlmostEqual(point[0],1.);self.assertAlmostEqual(point[1],2.1)
        lookalike=SimpleNamespace(prim_path='/obj',links={'door':link})
        with self.assertRaises(SkillError):binding.resolve([lookalike])
    def test_target_motion_tracks_same_link_and_replacement_aborts(self):
        pose=[(0.,0.,0.),(0.,0.,0.,1.)]
        link=SimpleNamespace(prim_path='/obj/link',get_position_orientation=lambda:tuple(pose))
        obj=SimpleNamespace(prim_path='/obj',links={'link':link})
        binding=LockedTarget.bind(obj,(0.,0.,0.),{'visual_mesh':'/obj/link/visuals/mesh'})
        pose[0]=(1.,0.,0.)
        selected,point=binding.resolve([obj])
        self.assertIs(selected,obj);self.assertEqual(point,(1.,0.,0.))
        obj.links={}
        with self.assertRaises(SkillError):binding.resolve([obj])


if __name__=='__main__':unittest.main()
