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
        route,status=compact_translation(origin,base,[*bounds,((.2,-.1,.2),(2.4,.1,.4))],lambda a,b:True)
        self.assertIsNone(route);self.assertEqual(status,'payload_too_large')

    def test_full_size_table_moves_around_robot_only_through_clear_sweeps(self):
        origin=(0.,-1.6,.84);base=(0.,0.,0.,0.)
        bounds=[((-.41,-2.27,.83),(.41,-.93,.85))]
        # Conservative body expanded by the complete tabletop radius.
        reserve=.42+math.hypot(.41,.67)
        checks=[]
        def clear(a,b):
            checks.append((a,b))
            vx,vy=b[0]-a[0],b[1]-a[1]
            d=vx*vx+vy*vy
            t=max(0.,min(1.,-(a[0]*vx+a[1]*vy)/d)) if d else 0.
            return math.hypot(a[0]+t*vx,a[1]+t*vy)>reserve
        route,status=compact_translation(origin,base,bounds,clear)
        self.assertEqual(status,'planned')
        self.assertTrue(checks)
        self.assertFalse(clear(origin,route[-1]))
        self.assertTrue(all(clear(a,b) for a,b in zip(route,route[1:])))
        self.assertAlmostEqual(route[-1][1],0.)
        self.assertLessEqual(math.dist(route[-1][:2],base[:2]),1.4)
        self.assertGreaterEqual(route[-1][2]-.01,.95)
        self.assertLessEqual(sum(math.dist(a,b) for a,b in zip(route,route[1:])),3.)
        blocked,status=compact_translation(origin,base,bounds,lambda a,b:False)
        self.assertIsNone(blocked);self.assertEqual(status,'blocked')

    def test_wide_payload_uses_lower_checked_pose_under_a_low_ceiling(self):
        origin=(1.6,0.,.84);base=(0.,0.,0.,0.)
        bounds=[((1.19,-.67,.83),(2.01,.67,.85))]
        def clear(a,b):
            return max(a[2],b[2])+.01 < .91
        route,status=compact_translation(origin,base,bounds,clear)
        self.assertEqual(status,'planned')
        self.assertTrue(all(clear(a,b) for a,b in zip(route,route[1:])))
        self.assertAlmostEqual(route[-1][2],.84)

    def test_obstacle_in_every_swept_route_preserves_original_posture(self):
        route,status=compact_translation((1.3,0.,.2),(0.,0.,0.,0.),
            [((1.25,-.05,.15),(1.35,.05,.25))],lambda a,b:False)
        self.assertIsNone(route);self.assertEqual(status,'blocked')

    def test_side_payload_moves_around_body_to_reduce_loaded_width(self):
        origin=(0.,.65,1.);base=(0.,0.,0.,0.)
        bounds=[((-.08,.57,.9),(.08,.73,1.1))]
        def clear(a,b):
            # Expanded circular body: endpoints alone are insufficient.
            vx,vy=b[0]-a[0],b[1]-a[1]
            t=max(0.,min(1.,-(a[0]*vx+a[1]*vy)/(vx*vx+vy*vy))) if vx*vx+vy*vy else 0.
            return math.hypot(a[0]+t*vx,a[1]+t*vy)>.5
        route,status=compact_translation(origin,base,bounds,clear)
        self.assertEqual(status,'planned')
        self.assertFalse(clear(origin,route[-1]))
        self.assertTrue(all(clear(a,b) for a,b in zip(route,route[1:])))
        self.assertAlmostEqual(route[-1][1],0.)
        self.assertGreater(route[-1][0],.55)
        self.assertLessEqual(sum(math.dist(a,b) for a,b in zip(route,route[1:])),2.)

    def test_blocked_front_keeps_safe_side_pose_and_propagates_cancellation(self):
        origin=(0.,.6,1.);base=(0.,0.,0.,0.)
        bounds=[((-.08,.52,.9),(.08,.68,1.1))]
        route,status=compact_translation(origin,base,bounds,lambda a,b:b[0]<.1)
        self.assertEqual(route,());self.assertEqual(status,'already_compact')
        def cancel():raise TimeoutError('cancelled')
        with self.assertRaises(TimeoutError):
            compact_translation(origin,base,bounds,lambda a,b:True,check=cancel)


@unittest.skipUnless(importlib.util.find_spec('torch'),'Pinned tensor dependency required')
class CarryAdapterTests(unittest.TestCase):
    def run_adapter(self,*,blocked=False,step_limit=180,prepared=False):
        import numpy as np
        import torch
        b=CompactCarry();b.torch=torch;b.steps=0;b._carry_contents=[];b.demo_motion=False
        pose=[torch.tensor([1.3,0.,.2]),torch.tensor([0.,0.,0.,1.])]
        points=torch.tensor([[x,y,z] for x in (1.25,1.35) for y in (-.05,.05) for z in (.15,.25)])
        held=SimpleNamespace(name='can',get_position_orientation=lambda:tuple(pose),links={
            'body':SimpleNamespace(is_meta_link=False,prim_path='/can',collision_boundary_points_world=points)})
        robot_link=SimpleNamespace(is_meta_link=False,prim_path='/robot',
            collision_boundary_points_world=torch.tensor([[x,y,z] for x in (-.2,.2) for y in (-.2,.2) for z in (.03,1.5)]))
        b.robot=SimpleNamespace(links={'base':robot_link},get_position_orientation=lambda:(torch.zeros(3),pose[1]),
                               get_joint_positions=lambda:torch.zeros(1),q_to_action=lambda q:q)
        b.env=SimpleNamespace(scene=SimpleNamespace(objects=[held,b.robot]))
        b.og=SimpleNamespace(sim=SimpleNamespace(get_sim_step_dt=lambda:1/30))
        b.deadline=SimpleNamespace(check=lambda **kw:None)
        b._get_held=lambda:held;b._anchored_operation=nullcontext
        b._local_pose=lambda:(0.,0.,0.)
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
        with patch.dict(sys.modules,modules):
            if prepared:
                route=((1.3,0.,.2),(1.3,0.,.38),(.65,0.,.7))
                report=b._execute_prepared_carry(held,{'route':route,'status':'compact'},step_limit)
            else:report=b._compact_carry(step_limit)
        self.assertEqual(len(records),1)
        self.assertIs(b._get_held(),held)
        return b,report,pose

    def test_packs_over_recorded_steps_and_preserves_grasp(self):
        b,report,pose=self.run_adapter()
        self.assertEqual(report['status'],'packed')
        self.assertGreater(b.steps,0);self.assertLessEqual(b.steps,180)
        self.assertLess(report['offset_after_m'],.7)

    def test_prepared_route_is_executed_over_steps_and_keeps_attachment(self):
        b,report,pose=self.run_adapter(prepared=True)
        self.assertTrue(report['shared_prediction_and_execution'])
        self.assertGreater(b.steps,6)
        self.assertLess(math.dist(pose[0].tolist(),(.65,0.,.7)),1e-6)

    def test_collision_or_insufficient_steps_never_moves_or_drops(self):
        for options,status in [({'blocked':True},'blocked'),({'step_limit':1},'not_moved_step_budget')]:
            b,report,pose=self.run_adapter(**options)
            self.assertEqual(report['status'],status);self.assertEqual(b.steps,0)
            self.assertAlmostEqual(float(pose[0][0]),1.3,places=6)
