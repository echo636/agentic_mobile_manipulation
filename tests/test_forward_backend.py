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
        b.primitives=SimpleNamespace(_empty_action=lambda:torch.zeros(5))
        b._get_held=lambda:None
        self.restores=0
        def restore(position,orientation):
            self.restores+=1
            self.position=position.clone()
            self.quat=orientation.clone()
        b._transfer_base_pose=restore
        b._step=lambda action:setattr(b,'steps',b.steps+1)
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

    def test_one_transfer_and_one_step_preserves_actual_physics_result(self):
        def step(action):
            self.b.steps+=1
            self.position[0]+=.12
        self.b._step=step
        result=self.b._execute_base_path([(0.,0.),(0.,.5)],0.,100)
        self.assertEqual(self.restores,1);self.assertEqual(self.b.steps,1)
        self.assertFalse(result['continuous_motion_simulated'])
        record=json.loads((self.b.output/'base_motion.jsonl').read_text())
        self.assertAlmostEqual(record['actual_position'][0],.12)
        self.assertEqual(result['nav_status'],'endpoint_applied')

    def test_cancelled_action_does_not_change_pose(self):
        def cancel():raise SkillError('episode_cancelled','cancelled')
        self.b.deadline.check=cancel
        with self.assertRaises(SkillError):self.b._execute_base_path([(0.,0.),(1.,0.)],0.,100)
        self.assertEqual(self.restores,0)

    def test_look_changes_yaw_without_planning_translation(self):
        before=self.position.clone()
        self.b._turn(90,100)
        self.assertTrue(self.torch.equal(before,self.position))
        self.assertAlmostEqual(float(self.quat[2]),math.sin(math.pi/4),places=6)
        self.assertEqual(self.restores,1);self.assertEqual(self.b.steps,1)
