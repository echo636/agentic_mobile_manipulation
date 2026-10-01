"""Regression cases derived from archived actuator failures, not task scores."""
import importlib.util
import math
import random
import types
import unittest
from contextlib import nullcontext
from unittest.mock import patch

from manipulation_agent.contracts import SkillError
from manipulation_agent.executors.carry import ControlledCarry, support_closure
from manipulation_agent.executors.gt_navigation import GridMap
from manipulation_agent.executors.placement import CheckedPlacement
from manipulation_agent.observations.rig import visible_rig_rays
from manipulation_agent.observations.boundary import public_execution_error


class GridSupercoverRegression(unittest.TestCase):
    def test_archived_valid_corner_is_consistent_for_plan_and_substep(self):
        cells=bytearray([1])*(30*45)
        g=GridMap(30,45,.05,(5.025,.025),bytes(cells))
        row,col=g.cell((5.775,1.425));cells[row*30+col]=0
        g=GridMap(30,45,.05,(5.025,.025),bytes(cells))
        a=(5.475000000000001,1.2250000000000014)
        b=(5.775000000000006,1.4750000000000014)
        # The ray passes just above the occupied square. The old distance
        # sampler invented a diagonal crossing inside this short substep.
        self.assertTrue(g.segment_free(a,b))
        self.assertTrue(g.segment_free((5.743898391723633,1.449082612991333),
                                        (5.756702205221132,1.4597522023788885)))

    def test_subdivision_and_direction_cannot_change_collision_decision(self):
        rng=random.Random(71);cells=bytes(int(rng.random()>.2) for _ in range(900))
        g=GridMap(30,30,.1,(-1.,-1.),cells)
        for _ in range(150):
            a=(rng.uniform(-.9,1.8),rng.uniform(-.9,1.8));b=(rng.uniform(-.9,1.8),rng.uniform(-.9,1.8))
            points=[tuple(x+(y-x)*i/17 for x,y in zip(a,b)) for i in range(18)]
            self.assertEqual(g.segment_free(a,b),all(g.segment_free(x,y) for x,y in zip(points,points[1:])))
            self.assertEqual(g.segment_free(a,b),g.segment_free(b,a))

    def test_tangent_edge_includes_adjacent_blocked_cell(self):
        g=GridMap(3,3,1.,(0.,0.),bytes([1,1,1,1,0,1,1,1,1]))
        self.assertFalse(g.segment_free((0.,.5),(2.,.5)))
        self.assertTrue(g.segment_free((0.,.49),(2.,.49)))


class MotorContractRegression(unittest.TestCase):
    def test_support_payload_closure_includes_nested_items_without_cycles(self):
        plate,pizza,cup,contents,unrelated=[object() for _ in range(5)]
        relationships={(pizza,plate):'OnTop',(cup,plate):'OnTop',(contents,cup):'Inside',(plate,pizza):'OnTop'}
        edges=support_closure(plate,[contents,unrelated,cup,pizza,plate],lambda c,p:relationships.get((c,p)))
        self.assertEqual({c for c,_,_ in edges},{pizza,cup,contents})
        self.assertEqual(len(edges),3)

    def test_selected_surface_failure_never_retries_without_the_point(self):
        b=CheckedPlacement();b._get_held=lambda:object();calls=[]
        def fail(held,target,steps,point,yaw):
            calls.append(point);raise SkillError('sampling_error','no local pose')
        b._try_place_on_top=fail
        states=types.ModuleType('omnigibson.object_states');states.OnTop=object()
        errors=types.ModuleType('omnigibson.action_primitives.action_primitive_set_base');errors.ActionPrimitiveError=RuntimeError
        with patch.dict('sys.modules',{'omnigibson.object_states':states,'omnigibson.action_primitives.action_primitive_set_base':errors}):
            with self.assertRaises(SkillError):b._checked_place_on_top(object(),700,point=(1,2,3))
        self.assertEqual(calls,[(1,2,3)])

    def test_real_camera_frustum_rejects_near_floor_blind_spot(self):
        self.assertEqual(visible_rig_rays((.5,0),math.pi,0,1.53,(0,0,0)),[])
        rays=visible_rig_rays((1.3,0),math.pi,0,1.53,(0,0,0))
        self.assertTrue(rays)
        self.assertTrue(all(.04<=u<=.96 and .04<=v<=.96 for _,_,(u,v) in rays))

    def test_invalid_start_feedback_does_not_suggest_changing_destination(self):
        error=public_execution_error(SkillError('navigation_invalid_start','private pose xyz'))
        self.assertEqual(error['code'],'navigation_invalid_start')
        self.assertNotIn('xyz',error['message'])
        self.assertIn('stop repeating',error['message'])


@unittest.skipUnless(importlib.util.find_spec('torch'),'Requires geometry environment')
class AnchoringAndPlacementRegression(unittest.TestCase):
    def test_plate_support_ray_ignores_food_but_rejects_unrelated_support(self):
        import torch
        def obj(name):
            return types.SimpleNamespace(name=name,links={'root':types.SimpleNamespace(prim_path='/'+name)})
        plate,pizza,robot,table=[obj(name) for name in ('plate','pizza','robot','table')]
        plate.aabb=(torch.tensor([-.2,-.2,0.]),torch.tensor([.2,.2,.02]))
        plate.get_linear_velocity=lambda:torch.zeros(3)
        b=CheckedPlacement();b.robot=robot;b.torch=torch;b._get_held=lambda:None
        sampling=types.ModuleType('omnigibson.utils.sampling_utils');seen=[];support=['/table']
        def raytest(start,end,ignore_bodies):
            seen.append(ignore_bodies)
            return {'hit':True,'rigidBody':support[0] if '/pizza' in ignore_bodies else '/pizza',
                    'position':torch.zeros(3),'normal':torch.tensor([0.,0.,1.])}
        sampling.raytest=raytest
        with patch.dict('sys.modules',{'omnigibson.utils.sampling_utils':sampling}):
            valid,_=b._selected_surface_support(plate,table,torch.zeros(3),True,[pizza])
            self.assertTrue(valid);self.assertIn('/pizza',seen[-1]);self.assertNotIn('/table',seen[-1])
            support[0]='/unrelated'
            valid,_=b._selected_surface_support(plate,table,torch.zeros(3),True,[pizza])
            self.assertFalse(valid)

    def test_sampler_physics_cannot_displace_base_and_wrapper_restores_on_error(self):
        import torch
        pose=torch.tensor([1.,2.,3.]);quat=torch.tensor([0.,0.,0.,1.]);calls=[]
        b=ControlledCarry();b.torch=torch;b._base_target=None;b._object_anchor=None
        b.robot=types.SimpleNamespace(get_position_orientation=lambda:(pose.clone(),quat.clone()),
            get_joint_positions=lambda:torch.zeros(5),base_idx=torch.tensor([0,1]))
        def physics():pose.add_(10);calls.append('physics')
        b.og=types.SimpleNamespace(sim=types.SimpleNamespace(step_physics=physics))
        b._restore_base_target=lambda:pose.copy_(b._base_target['position'])
        with self.assertRaisesRegex(ValueError,'injected'):
            with b._anchored_operation():
                b.og.sim.step_physics()
                self.assertTrue(torch.equal(pose,torch.tensor([1.,2.,3.])))
                raise ValueError('injected')
        self.assertEqual(calls,['physics']);self.assertIs(b.og.sim.step_physics,physics)
        self.assertIsNone(b._base_target);self.assertTrue(torch.equal(pose,torch.tensor([1.,2.,3.])))

    def test_whole_object_ontop_cannot_override_wrong_selected_shelf(self):
        import torch
        states=types.ModuleType('omnigibson.object_states')
        for name in ('OnTop','Touching','VerticalAdjacency'):setattr(states,name,type(name,(),{}))
        errors=types.ModuleType('omnigibson.action_primitives.action_primitive_set_base');errors.ActionPrimitiveError=RuntimeError
        target=object();held=types.SimpleNamespace(name='held',states={
            states.OnTop:types.SimpleNamespace(get_value=lambda t:True),
            states.Touching:types.SimpleNamespace(get_value=lambda t:True),
            states.VerticalAdjacency:types.SimpleNamespace(get_value=lambda:types.SimpleNamespace(negative_neighbors=[target],positive_neighbors=[]))},
            set_position_orientation=lambda *a:None,keep_still=lambda:None,get_position_orientation=lambda:(torch.zeros(3),torch.tensor([0.,0.,0.,1.])))
        target=types.SimpleNamespace(name='fridge');b=CheckedPlacement();b.ideal_carry=True;b._carry_contents=[]
        b._placement_context=nullcontext;b._surface_pose=lambda *a:(torch.zeros(3),torch.tensor([0.,0.,0.,1.]))
        b._carry_detach=lambda:None;b._relocate_contents=lambda *a:None;b._step=lambda action:None
        b.robot=types.SimpleNamespace(get_joint_positions=lambda:None,q_to_action=lambda q:None)
        b._selected_surface_support=lambda *a:(False,{'selected_height_error_m':.692})
        b._placement_record=lambda data:None;b._get_held=lambda:None;b._verify_payload=lambda x:None
        with patch.dict('sys.modules',{'omnigibson.object_states':states,'omnigibson.action_primitives.action_primitive_set_base':errors}):
            with self.assertRaisesRegex(SkillError,'selected surface'):
                b._try_place_on_top(held,target,1,torch.zeros(3))


if __name__=='__main__':unittest.main()
