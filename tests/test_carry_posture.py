"""Carry packing geometry and real adapter sequencing; no simulator acceptance."""
from contextlib import nullcontext
import importlib.util
import math
import sys
from types import SimpleNamespace, ModuleType
import unittest
from unittest.mock import patch
from manipulation_agent.executors.carry_posture import compact_translation, translation_samples, CompactCarry


class CarryRouteTests(unittest.TestCase):
    def test_whole_assembly_size_and_swept_route_are_required(self):
        origin=(1.3,0.,.2);base=(0.,0.,0.,0.)
        bounds=[((1.25,-.05,.15),(1.35,.05,.25))]
        checks=[]
        route,status=compact_translation(origin,base,bounds,lambda a,b:checks.append((a,b)) or True)
        self.assertEqual(status,'planned');self.assertTrue(checks)
        self.assertLess(math.dist(route[-1][:2],base[:2]),.7)
        self.assertGreaterEqual(route[-1][2]-.05,.65)
        samples=list(translation_samples(route,.01))
        self.assertTrue(all(math.dist(a,b)<=.010001 for a,b in zip([origin,*samples],samples)))
        self.assertLess(math.dist(samples[-1],route[-1]),1e-12)
        route,status=compact_translation(origin,base,[*bounds,((.5,-.1,.2),(2.,.1,.4))],lambda a,b:True)
        self.assertIsNone(route);self.assertEqual(status,'payload_too_large')

    def test_obstacle_in_every_swept_route_preserves_original_posture(self):
        route,status=compact_translation((1.3,0.,.2),(0.,0.,0.,0.),
            [((1.25,-.05,.15),(1.35,.05,.25))],lambda a,b:False)
        self.assertIsNone(route);self.assertEqual(status,'blocked')


@unittest.skipUnless(importlib.util.find_spec('torch'),'Pinned tensor dependency required')
class CarryAdapterTests(unittest.TestCase):
    def run_adapter(self,*,blocked=False,step_limit=180):
        import numpy as np
        import torch
        b=CompactCarry();b.torch=torch;b.steps=0;b._carry_contents=[];b.demo_motion=False
        pose=[torch.tensor([1.3,0.,.2]),torch.tensor([0.,0.,0.,1.])]
        points=torch.tensor([[x,y,z] for x in (1.25,1.35) for y in (-.05,.05) for z in (.15,.25)])
        held=SimpleNamespace(name='can',get_position_orientation=lambda:tuple(pose),links={
            'body':SimpleNamespace(is_meta_link=False,prim_path='/can',collision_boundary_points_world=points)})
        robot_link=SimpleNamespace(is_meta_link=False,prim_path='/robot')
        b.robot=SimpleNamespace(links={'base':robot_link},get_position_orientation=lambda:(torch.zeros(3),pose[1]),
                               get_joint_positions=lambda:torch.zeros(1),q_to_action=lambda q:q)
        b.env=SimpleNamespace(scene=SimpleNamespace(objects=[held,b.robot]))
        b.og=SimpleNamespace(sim=SimpleNamespace(get_sim_step_dt=lambda:1/30))
        b.deadline=SimpleNamespace(check=lambda **kw:None)
        b._get_held=lambda:held;b._anchored_operation=nullcontext
        b._carry_follow=lambda:pose.__setitem__(0,b._carry_relative[0].clone())
        b._step=lambda a:setattr(b,'steps',b.steps+1)
        records=[];b._carry_record=lambda **kw:records.append(kw)
        def overlap(half,center,rotation,callback,any_hit):
            # Own payload ignored; robot remains a real blocker in this query.
            callback(SimpleNamespace(rigid_body='/can'))
            if blocked or center[0]-half[0]<.42:callback(SimpleNamespace(rigid_body='/robot'))
        modules={name:ModuleType(name) for name in ('omnigibson','omnigibson.lazy','omnigibson.utils','omnigibson.utils.transform_utils')}
        modules['omnigibson'].lazy=modules['omnigibson.lazy'];modules['omnigibson'].utils=modules['omnigibson.utils']
        modules['omnigibson.utils'].transform_utils=modules['omnigibson.utils.transform_utils']
        modules['omnigibson.lazy'].omni=SimpleNamespace(physx=SimpleNamespace(
            get_physx_scene_query_interface=lambda:SimpleNamespace(overlap_box=overlap)))
        transforms=modules['omnigibson.utils.transform_utils']
        transforms.quat2mat=lambda q:torch.eye(3)
        transforms.relative_pose_transform=lambda pos,ori,*_: (pos,ori)
        with patch.dict(sys.modules,modules):report=b._compact_carry(step_limit)
        self.assertEqual(len(records),1)
        self.assertIs(b._get_held(),held)
        return b,report,pose

    def test_packs_over_recorded_steps_and_preserves_grasp(self):
        b,report,pose=self.run_adapter()
        self.assertEqual(report['status'],'packed')
        self.assertGreater(b.steps,0);self.assertLessEqual(b.steps,180)
        self.assertLess(report['offset_after_m'],.7)

    def test_collision_or_insufficient_steps_never_moves_or_drops(self):
        for options,status in [({'blocked':True},'blocked'),({'step_limit':1},'not_moved_step_budget')]:
            b,report,pose=self.run_adapter(**options)
            self.assertEqual(report['status'],status);self.assertEqual(b.steps,0)
            self.assertAlmostEqual(float(pose[0][0]),1.3,places=6)
