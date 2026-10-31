"""Under placement releases once and evaluates the resulting physical state."""
import sys
import types
import unittest
from unittest.mock import Mock, patch

import torch

from manipulation_agent.contracts import SkillError
from manipulation_agent.executors.placement import CheckedPlacement


class UnderPlacementTests(unittest.TestCase):
    def setUp(self):
        self.Under, self.OnTop = type('Under', (), {}), type('OnTop', (), {})
        states = types.ModuleType('omnigibson.object_states')
        states.Under, states.OnTop = self.Under, self.OnTop
        sampling = types.ModuleType('omnigibson.utils.sampling_utils')
        sampling.raytest = lambda start, end, **kwargs: {
            'hit': True, 'normal': [0., 0., 1.], 'rigidBody': '/support',
            'position': [float(start[0]), float(start[1]), 2.]}
        modules = patch.dict(sys.modules, {
            'omnigibson': types.ModuleType('omnigibson'),
            'omnigibson.object_states': states,
            'omnigibson.utils': types.ModuleType('omnigibson.utils'),
            'omnigibson.utils.sampling_utils': sampling})
        modules.start(); self.addCleanup(modules.stop)

        class Object:
            def __init__(self, name, low, high):
                self.name = name
                self.aabb = torch.tensor(low), torch.tensor(high)
                self.links = {'root': types.SimpleNamespace(prim_path='/'+name, is_meta_link=False)}
                self.states = {}

        self.held = Object('box', [3., 3., 3.], [3.4, 3.4, 3.4])
        self.target = Object('table', [0., 0., 2.], [2., 2., 3.])
        self.support = Object('support', [0., 0., 1.8], [2., 2., 2.])
        self.robot = Object('robot', [4., 4., 2.], [5., 5., 4.])
        self.held.get_position_orientation = lambda: (torch.tensor([3.2, 3.2, 3.2]), torch.tensor([0., 0., 0., 1.]))
        self.held.set_position_orientation = Mock()
        self.held.keep_still = Mock()
        self.under = Mock(); self.under.get_value.return_value = True
        self.top = Mock(); self.top.get_value.return_value = True
        self.held.states = {self.Under: self.under, self.OnTop: self.top}
        self.backend = CheckedPlacement()
        b = self.backend
        b.robot, b.torch = self.robot, torch
        b.env = types.SimpleNamespace(scene=types.SimpleNamespace(objects=[self.held, self.target, self.support, self.robot]))
        b.og = types.SimpleNamespace(sim=types.SimpleNamespace(get_physics_dt=lambda: 1/120))
        b.deadline = types.SimpleNamespace(check=lambda: None)
        b._get_held = lambda: self.held
        b._carry_contents, b._carry_dependencies = [], []
        b._inside_pose_overlap_ranker = lambda *args: lambda pos, quat: int(pos[0] > .6)
        b._carry_detach = Mock()
        b._relocate_contents = Mock()
        b._placement_record = Mock()
        b._placement_contact_report = lambda *args, **kw: {'admissible': True}
        b._verify_payload = Mock()
        b._step = Mock()
        b.primitives = types.SimpleNamespace(_empty_action=lambda: [])

    def test_occupied_center_uses_clear_support_without_native_sampling(self):
        result = self.backend._checked_place_under(self.target, 50)
        position, _ = self.held.set_position_orientation.call_args.args
        self.assertAlmostEqual(float(position[0]), .5)
        self.assertGreater(float(position[2]), 2.2)
        self.assertLess(float(position[2]), 2.21)
        self.held.set_position_orientation.assert_called_once()
        self.backend._relocate_contents.assert_called_once()
        self.backend._carry_detach.assert_called_once()
        self.under.set_value.assert_not_called()
        self.top.get_value.assert_called_once_with(self.support)
        self.assertEqual(result['failure_policy'], 'retain_physical_result')

    def test_missing_support_keeps_grasp_and_scene_unchanged(self):
        self.backend._under_candidates = lambda *args: []
        with self.assertRaises(SkillError):
            self.backend._checked_place_under(self.target, 50)
        self.backend._carry_detach.assert_not_called()
        self.held.set_position_orientation.assert_not_called()

    def test_settling_failure_is_not_retried_or_rewound(self):
        def step(_):
            self.under.get_value.return_value = False
        self.backend._step.side_effect = step
        with self.assertRaises(SkillError) as caught:
            self.backend._checked_place_under(self.target, 50)
        self.assertEqual(caught.exception.code, 'postcondition_error')
        self.held.set_position_orientation.assert_called_once()
        self.under.set_value.assert_not_called()
        self.backend._verify_payload.assert_not_called()
