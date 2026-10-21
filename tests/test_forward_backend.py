"""Exercise the real adapter motion loop with CPU tensors and pose feedback.

Only simulation/robot I/O is replaced; this is not simulator acceptance.
"""
import importlib.util
import json
import math
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace, ModuleType
import unittest
from unittest.mock import patch

from manipulation_agent.contracts import SkillError
from manipulation_agent.executors.omnigibson_rgb import RGBBackend
from manipulation_agent.executors.gt_navigation import GridMap, NavigationPlan


@unittest.skipUnless(importlib.util.find_spec('torch') and importlib.util.find_spec('cv2'),
                     'Requires pinned CPU tensor/geometry dependencies')
class ForwardBackendTests(unittest.TestCase):
    def setUp(self):
        # Load native extensions before patch.dict restores sys.modules.
        # Re-importing OpenCV after removing only its Python modules is unsafe.
        import cv2
        import numpy as np
        import torch
        self.torch=torch
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.b=b=RGBBackend.__new__(RGBBackend)
        b.output=Path(self.temp.name);b.steps=0;b.torch=torch;b._base_target=None
        self.position=torch.tensor([0.,0.,.01]);self.quat=torch.tensor([0.,0.,0.,1.])
        self.joints=torch.zeros(5)
        b.robot=SimpleNamespace(get_position_orientation=lambda:(self.position.clone(),self.quat.clone()),
            get_joint_positions=lambda:self.joints.clone(),base_idx=torch.tensor([0,1,2]),q_to_action=lambda q:q)
        b.deadline=SimpleNamespace(check=lambda:None)
        b.og=SimpleNamespace(sim=SimpleNamespace(get_sim_step_dt=lambda:1/30))
        b._get_held=lambda:None
        self.restores=0
        def restore():
            self.restores+=1
            self.position=b._base_target['position'].clone()
            self.quat=b._base_target['orientation'].clone()
        b._restore_base_target=restore
        b._step=lambda action:setattr(b,'steps',b.steps+1)
        raw=np.ones((81,81),dtype='uint8')
        self.grid=GridMap(81,81,.05,(-2.,-2.),raw.tobytes())
        b._navigation_support=raw
        b._navigation_layers=[(raw,np.array([[-.15,-.1],[.15,-.1],[.15,.1],[-.15,.1]]))]
        b._navigation_layer_yaw=0.
        transforms=ModuleType('omnigibson.utils.transform_utils')
        def quat2mat(q):
            yaw=2*math.atan2(float(q[2]),float(q[3]));c,s=math.cos(yaw),math.sin(yaw)
            return torch.tensor([[c,-s,0.],[s,c,0.],[0.,0.,1.]])
        transforms.quat2mat=quat2mat
        transforms.euler2quat=lambda v:torch.tensor([0.,0.,math.sin(float(v[2])/2),math.cos(float(v[2])/2)])
        modules={name:ModuleType(name) for name in ('omnigibson','omnigibson.utils')}
        modules['omnigibson'].utils=modules['omnigibson.utils']
        modules['omnigibson.utils'].transform_utils=transforms
        modules['omnigibson.utils.transform_utils']=transforms
        context=patch.dict(sys.modules,modules);context.start();self.addCleanup(context.stop)

    def plan(self,mode='forward'):
        return NavigationPlan(((0.,0.),(0.,.5)),(0.,.5),(0.,.5),0.,.5,0.,1,1,0.,0,
                              travel_yaw=0.,motion_mode=mode)

    def test_adapter_turns_to_path_and_restores_endpoint_view(self):
        result=self.b._execute_gt_plan(self.grid,self.plan(),500)
        self.assertEqual(result['motion_policy'],'turn_then_drive_forward_v1')
        self.assertFalse(result['physical_controller'])
        self.assertLess(result['final_position_error_m'],.002)
        self.assertLess(result['final_yaw_error_rad'],.001)
        records=[json.loads(line) for line in (self.b.output/'base_motion.jsonl').read_text().splitlines()]
        self.assertGreater(records[0]['commanded_yaw'],0.)
        self.assertEqual(records[0]['commanded_position'][:2],[0.,0.])
        previous=(0.,0.,0.)
        for event in records:
            x,y=event['actual_position'][:2];q=event['actual_orientation']
            yaw=2*math.atan2(q[2],q[3]);dx,dy=x-previous[0],y-previous[1]
            if math.hypot(dx,dy)>.002:
                self.assertGreater(dx*math.cos(previous[2])+dy*math.sin(previous[2]),0.)
                self.assertLess(abs(-dx*math.sin(previous[2])+dy*math.cos(previous[2])),.002)
            previous=(x,y,yaw)
        self.assertIsNone(self.b._base_target)

    def test_all_turn_and_translation_phases_share_step_budget(self):
        with self.assertRaises(SkillError) as caught:
            self.b._execute_gt_plan(self.grid,self.plan(),40)
        self.assertEqual(caught.exception.code,'action_timeout')
        self.assertTrue(caught.exception.changed)
        self.assertLessEqual(self.b.steps,40)
        self.assertIsNone(self.b._base_target)

    def test_contact_recovery_is_explicit_fixed_heading_exception(self):
        result=self.b._execute_gt_plan(self.grid,self.plan('clearance_retreat'),500)
        self.assertEqual(result['motion_policy'],'clearance_retreat')
        self.assertIsNone(result['forward_route_method'])
        records=[json.loads(line) for line in (self.b.output/'base_motion.jsonl').read_text().splitlines()]
        self.assertTrue(all(r['commanded_yaw']==0. for r in records))

    def test_blocked_orientation_fails_before_any_pose_mutation(self):
        self.b._navigation_layers[0][0][:]=0
        with self.assertRaises(SkillError) as caught:
            self.b._execute_gt_plan(self.grid,self.plan(),500)
        self.assertEqual(caught.exception.code,'navigation_invalid_start')
        self.assertFalse(caught.exception.changed)
        self.assertEqual(self.restores,0)

    def test_look_checks_swept_payload_and_never_translates(self):
        import numpy as np
        b=self.b
        b.env=SimpleNamespace(scene=SimpleNamespace(trav_map=SimpleNamespace(floor_heights=[0.])))
        raw=b._navigation_layers[0][0]
        # Long carried item clears both end orientations but clips the diagonal.
        b._navigation_layers=[(raw,np.array([[-.4,-.03],[.4,-.03],[.4,.03],[-.4,.03]]))]
        raw[45,45]=0
        with patch('manipulation_agent.executors.live_gt_map.build_navigation_grid',return_value=(self.grid,{})):
            with self.assertRaises(SkillError) as caught:b._turn(90,200)
            self.assertEqual(caught.exception.code,'look_rotation_blocked')
            self.assertFalse(caught.exception.changed)
            self.assertEqual(self.restores,0)
            raw[45,45]=1
            result=b._turn(90,200)
        self.assertEqual(result['forward_route_method'],'checked_in_place_look')
        self.assertEqual(result['actual_path_distance_m'],0.)
        self.assertLess(result['final_yaw_error_rad'],.001)

    def test_look_certificate_start_mismatch_does_not_plan_a_detour(self):
        from manipulation_agent.executors.forward_navigation import ForwardRoute
        route=ForwardRoute((self.plan(),),0.,0,'fixture',start=(.1,0.,0.))
        with self.assertRaises(SkillError) as caught:
            self.b._execute_gt_plan(self.grid,self.plan(),100,prepared_route=route,require_prepared_route=True)
        self.assertEqual(caught.exception.code,'look_rotation_blocked')
        self.assertEqual(self.restores,0)

    def test_incomplete_candidate_pool_does_not_claim_geometric_impossibility(self):
        from manipulation_agent.executors.local_manipulation import LocalLimits,LocalOperation
        from manipulation_agent.executors.heading_collision import HeadingCollisionChecker
        from manipulation_agent.executors.gt_navigation import NavigationError
        b=self.b;b.local_limits=LocalLimits();b.deadline=SimpleNamespace(check=lambda **kw:None)
        b.ideal_carry=False;b.demo_motion=False
        b._approach_visible=lambda *a,**kw:True
        records=[];b._local_record=lambda **kw:records.append(kw)
        def geometry(op):
            return (self.grid,HeadingCollisionChecker(self.grid,b._navigation_layers,0.,b._navigation_support),
                    {'held_object_footprint_included':False},(),0)
        b._local_geometry=geometry
        for reason,expected in [('navigation_search_exhausted','local_adjustment_exhausted'),
                                ('navigation_unreachable','local_adjustment_exhausted')]:
            op=LocalOperation(b,'open',None,600,{})
            with patch('manipulation_agent.executors.forward_navigation.plan_forward_route',
                       side_effect=NavigationError('fixture incomplete search',reason)), \
                 patch('manipulation_agent.executors.forward_navigation.checked_connection',return_value=None), \
                 patch('manipulation_agent.executors.point_route_projection.project_forward_point',return_value=None):
                with self.assertRaises(SkillError) as caught:
                    b._local_reposition('open',object(),self.torch.tensor([1.55,0.,.8]),op)
            self.assertEqual(caught.exception.code,expected)
            self.assertEqual(self.restores,0)
        self.assertTrue(any(r.get('route_search')=='shared_target_stances' for r in records))

    def test_local_operation_drives_then_executes_once_without_macro_navigation(self):
        from manipulation_agent.executors.local_manipulation import LocalLimits,LocalOperation
        from manipulation_agent.executors.heading_collision import HeadingCollisionChecker
        b=self.b;b.local_limits=LocalLimits();b.navigation_distance=0.
        b.deadline=SimpleNamespace(check=lambda **kwargs:None)
        b._carry_contents=[];b.demo_motion=False;b.ideal_carry=False
        target=object();point=self.torch.tensor([1.55,0.,.8]);effects=[];captures=[]
        binding=SimpleNamespace(grounding={},describe=lambda:{'scope':'cpu_fixture'})
        b._resolve_local_target=lambda lock:(target,point)
        b._manipulation_preconditions=lambda *args:None
        def geometry(op):
            checker=HeadingCollisionChecker(self.grid,b._navigation_layers,0.,b._navigation_support,
                                             check_cancelled=op.check)
            return self.grid,checker,{'held_object_footprint_included':False},(),0
        b._local_geometry=geometry
        b._approach_visible=lambda *a,**kw:True
        b._local_reobserve=lambda lock:captures.append(lock)
        b._navigate=lambda *a,**kw:self.fail('Micropositioning called macro navigation')
        def execute(primitive,obj,selected,grounding,max_steps,**kwargs):
            self.assertIs(obj,target)
            self.assertLessEqual(float(self.torch.linalg.norm(self.position[:2]-selected[:2])),1.4)
            effects.append(obj)
            return {'postcondition':'fixture_verified'}
        b._execute_bound_manipulation=execute
        op=LocalOperation(b,'open',binding,600,{})
        b._local_operation=op
        result=op.run()
        self.assertEqual(effects,[target]);self.assertEqual(captures,[binding])
        self.assertEqual(result['local_manipulation']['repositions'],1)
        self.assertLessEqual(result['local_manipulation']['path_m'],2.)
        self.assertGreater(float(self.position[0]),.14)
        self.assertIsNone(b._base_target)

    def test_local_work_budget_prevents_motion_before_setter(self):
        from manipulation_agent.executors.local_manipulation import LocalLimits,LocalOperation
        b=self.b;b.local_limits=LocalLimits(max_env_steps=1)
        b.deadline=SimpleNamespace(check=lambda **kwargs:None)
        op=LocalOperation(b,'open',None,600,{})
        b._local_operation=op
        b.steps=1
        with self.assertRaises(SkillError) as caught:b._execute_gt_plan(self.grid,self.plan(),500)
        self.assertEqual(caught.exception.code,'local_adjustment_exhausted')
        self.assertEqual(float(self.torch.linalg.norm(self.position[:2])),0.)
        self.assertEqual(self.restores,0)


if __name__=='__main__':unittest.main()
