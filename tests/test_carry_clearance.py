"""Ceiling, payload and failed-query regressions for optional carry lifting."""
from copy import deepcopy
from contextlib import nullcontext
import importlib.util
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from manipulation_agent.executors.carry_clearance import choose_lift, assembly_lift
from manipulation_agent.executors.carry import ControlledCarry
from manipulation_agent.contracts import SkillError


class LiftClearanceTests(unittest.TestCase):
    def test_low_ceiling_limits_lift_and_leaves_input_untouched(self):
        bounds = [([0., 0., .9], [.2, .2, 1.05])]
        before = deepcopy(bounds)
        self.assertEqual(choose_lift(bounds, lambda low, high: high[2] >= 1.12), .045)
        self.assertEqual(bounds, before)

    def test_payload_ceiling_is_checked_even_when_root_is_clear(self):
        bounds = [([0., 0., .9], [.2, .2, 1.]), ([0., 0., 1.], [.1, .1, 1.1])]
        self.assertEqual(choose_lift(bounds, lambda low, high: high[2] >= 1.12), .01)

    def test_narrow_enclosure_keeps_original_pose(self):
        self.assertEqual(choose_lift([([0, 0, 0], [1, 1, 1])], lambda *args: True), 0.)

    def test_clearance_query_failure_never_becomes_an_accepted_lift(self):
        def failure(*args):
            raise RuntimeError('unavailable query')
        with self.assertRaisesRegex(RuntimeError, 'unavailable query'):
            choose_lift([([0, 0, 0], [1, 1, 1])], failure)

    def test_nonfinite_or_missing_geometry_cannot_be_treated_as_free(self):
        for bounds in ([], [([0, 0, 0], [1, 1, float('nan')])], [([0, 0, 0], [1, 1, 0])]):
            with self.subTest(bounds=bounds), self.assertRaises(ValueError):
                choose_lift(bounds, lambda *args: False)

    @unittest.skipUnless(importlib.util.find_spec('torch'), 'Requires geometry environment')
    def test_live_adapter_keeps_foreign_bodies_and_captured_particle_extent(self):
        import torch
        own = SimpleNamespace(prim_path='/bag/base', is_meta_link=False)
        helper = SimpleNamespace(prim_path='/oven/fill', is_meta_link=True)
        obj = SimpleNamespace(name='bag', links={'base': own},
                              aabb=(torch.tensor([0., 0., .9]), torch.tensor([.2, .2, 1.])))
        oven = SimpleNamespace(links={'fill': helper})
        system = SimpleNamespace(name='material', particles_view=SimpleNamespace(prim_paths=['/grain']),
            particle_contact_radius=.01,
            get_particles_position_orientation=lambda: (torch.tensor([[.1, .1, 1.08]]), None))
        particle = SimpleNamespace(system=system, paths=('/grain',))
        backend = SimpleNamespace(env=SimpleNamespace(scene=SimpleNamespace(
            objects=[obj, oven], active_systems={'material': system})))
        records = []
        backend._carry_record = lambda **row: records.append(row)

        def overlap(half, center, orientation, callback, any_hit):
            for path in ('/bag/base', '/oven/fill', '/grain'):
                callback(SimpleNamespace(rigid_body=path))
            if center[2] + half[2] > 1.12:
                callback(SimpleNamespace(rigid_body='/oven/ceiling'))
        lazy = ModuleType('omnigibson.lazy')
        lazy.omni = SimpleNamespace(physx=SimpleNamespace(
            get_physx_scene_query_interface=lambda: SimpleNamespace(overlap_box=overlap)))
        with patch.dict(sys.modules, {'omnigibson': ModuleType('omnigibson'), 'omnigibson.lazy': lazy}):
            self.assertEqual(assembly_lift(backend, obj, [], [particle]), .02)
        self.assertTrue(records[-1]['rejected_sweeps'])
        self.assertEqual(records[-1]['rejected_sweeps'][0]['bodies'], ['/oven/ceiling'])
        self.assertFalse(records[-1]['extraction_path_certified'])


@unittest.skipUnless(importlib.util.find_spec('torch'), 'Requires geometry environment')
class FailedGraspTests(unittest.TestCase):
    def fixture(self):
        import torch
        class Body:
            fixed_base=False
            n_joints=0
            states={}
            def __init__(self,name):
                self.name=name;self.position=torch.tensor([0.,0.,1.])
            def get_position_orientation(self):
                return self.position.clone(),torch.tensor([0.,0.,0.,1.])
            def set_position_orientation(self,p,q):
                self.position=p.clone()
            def keep_still(self):pass
            def get_joint_positions(self):return torch.zeros(1)
            def q_to_action(self,q):return q
        obj,robot=Body('bag'),Body('robot')
        b=ControlledCarry();b.torch=torch;b.robot=robot;b.ideal_carry=True
        b._ideal_held=None;b._carry_contents=[];b._carry_particles=[];b.frames_revision=0
        b.env=SimpleNamespace(scene=SimpleNamespace(objects=[obj,robot]))
        b._anchored_operation=lambda:nullcontext();b._carry_record=lambda **kwargs:None
        b._step=lambda action:obj.position.add_(torch.tensor([0.,0.,.03]))
        states=ModuleType('omnigibson.object_states')
        for name in ['Inside','OnTop','Touching','AttachedTo']:setattr(states,name,type(name,(),{}))
        utils=ModuleType('omnigibson.utils')
        utils.transform_utils=SimpleNamespace(relative_pose_transform=lambda p,q,b,bq:(p-b,q),
                                             pose_transform=lambda b,bq,p,q:(b+p,q))
        return b,obj,{'omnigibson.object_states':states,'omnigibson.utils':utils}

    def test_failed_support_check_drops_only_control_relationship(self):
        b,obj,modules=self.prepared_fixture()
        with patch.dict(sys.modules,modules), \
             patch('manipulation_agent.executors.carry.snapshot_particles',return_value=[]), \
             patch.object(b,'_execute_prepared_carry',side_effect=lambda *args:b._step(None)), \
             patch('manipulation_agent.executors.carry.verify_particles',
                   side_effect=SkillError('postcondition_error','foreign support')):
            with self.assertRaises(SkillError):b._ideal_grasp(obj,1)
        self.assertIsNone(b._ideal_held)
        self.assertEqual(b._carry_particles,[])
        self.assertEqual(b.frames_revision,-1)
        self.assertAlmostEqual(float(obj.position[2]),1.03,places=5)

    def test_already_held_cannot_bypass_particle_postconditions(self):
        b,obj,modules=self.fixture();b._ideal_held=obj
        with patch.dict(sys.modules,modules), \
             patch('manipulation_agent.executors.carry.verify_particles',
                   side_effect=SkillError('postcondition_error','foreign support')) as verify:
            with self.assertRaises(SkillError):b._ideal_grasp(obj,1)
        verify.assert_called_once();self.assertIsNone(b._ideal_held)

    def test_occupied_hand_rejection_retains_previous_valid_grasp(self):
        b,obj,modules=self.fixture();previous=object();b._ideal_held=previous
        with patch.dict(sys.modules,modules):
            with self.assertRaises(SkillError) as raised:b._ideal_grasp(obj,1)
        self.assertEqual(raised.exception.code,'hand_occupied');self.assertIs(b._ideal_held,previous)

    def prepared_fixture(self):
        b,obj,modules=self.fixture()
        b._local_pose=lambda:(0.,0.,0.)
        b._local_operation=SimpleNamespace(carry_plan={
            'pose':(0.,0.,0.),'route':[(0.,0.,1.),(0.,0.,1.02)],
            'status':'instant_pose_transfer','transfer_mode':'instant_pose_transfer'})
        b._validate_carry_destination=lambda *args:None
        b.og=SimpleNamespace(sim=SimpleNamespace(get_sim_step_dt=lambda:.1))
        return b,obj,modules

    def test_prepared_grasp_executes_destination_transfer_without_initial_lift(self):
        b,obj,modules=self.prepared_fixture()
        with patch.dict(sys.modules,modules), \
             patch('manipulation_agent.executors.carry.snapshot_particles',return_value=[]), \
             patch('manipulation_agent.executors.carry.verify_particles'), \
             patch.object(b,'_execute_prepared_carry',return_value={'status':'instant_pose_transfer'}) as execute:
            result=b._ideal_grasp(obj,20)
        execute.assert_called_once_with(obj,b._local_operation.carry_plan,20)
        self.assertEqual(result['carry_posture']['status'],'instant_pose_transfer')
        self.assertIs(b._ideal_held,obj)
        self.assertAlmostEqual(float(obj.position[2]),1.)

    def test_failed_instant_transfer_cannot_become_already_held_on_retry(self):
        b,obj,modules=self.prepared_fixture()
        with patch.dict(sys.modules,modules), \
             patch('manipulation_agent.executors.carry.snapshot_particles',return_value=[]), \
             patch.object(b,'_execute_prepared_carry',side_effect=SkillError(
                 'local_adjustment_blocked','Changed route',changed=True)):
            with self.assertRaises(SkillError) as raised:b._ideal_grasp(obj,20)
        self.assertEqual(raised.exception.code,'local_adjustment_blocked')
        self.assertTrue(raised.exception.changed)
        self.assertIsNone(b._ideal_held)
        self.assertEqual(b.frames_revision,-1)

    def test_prepared_route_does_not_bypass_particle_postconditions(self):
        b,obj,modules=self.prepared_fixture()
        with patch.dict(sys.modules,modules), \
             patch('manipulation_agent.executors.carry.snapshot_particles',return_value=[]), \
             patch.object(b,'_execute_prepared_carry',return_value={'status':'compact'}), \
             patch('manipulation_agent.executors.carry.verify_particles',
                   side_effect=SkillError('postcondition_error','foreign support')):
            with self.assertRaises(SkillError):b._ideal_grasp(obj,20)
        self.assertIsNone(b._ideal_held)
        self.assertEqual(b.frames_revision,-1)


if __name__ == '__main__':
    unittest.main()
