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


if __name__=='__main__':unittest.main()
