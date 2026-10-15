"""Hybrid navigation contracts; CPU fixtures, not simulator/task acceptance."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace, ModuleType
from unittest.mock import Mock, patch

import numpy as np

from manipulation_agent.contracts import SkillError
from manipulation_agent.deadline import EpisodeDeadline
from manipulation_agent.executors.omnigibson_rgb import RGBBackend


class NativeErrorGroup(Exception):
    def __init__(self):
        self.exceptions = [SimpleNamespace(reason=SimpleNamespace(name='PLANNING_ERROR'))]
        super().__init__('No native stance')


class SymbolicNavigationTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        b = self.b = RGBBackend.__new__(RGBBackend)
        b.output = Path(folder.name)
        b.deadline = EpisodeDeadline()
        b.steps = b.sampling_physics_steps = 0
        b.frames_revision = 0
        b.ideal_carry = True
        b._navigation_action = 'native_navigate_to'
        b._navigation_attempts = 3
        b._navigation_error_group = NativeErrorGroup
        self.position = np.array([0., 0., .01])
        self.payload_offset = np.array([.2, 0., 1.])
        self.payload = self.position + self.payload_offset
        b.robot = SimpleNamespace(get_position_orientation=lambda: (self.position.copy(), None))
        b.og = SimpleNamespace(sim=SimpleNamespace(step_physics=Mock()))
        self.original_physics = b.og.sim.step_physics
        self.effects = []
        self.target = SimpleNamespace(name='private_target')
        self.args = []
        self.generator_closed = False

        def follow():
            self.payload[:] = self.position + self.payload_offset
            self.effects.append('carry_follow')
        b._carry_follow = follow

        def step(action):
            b._carry_follow()  # Same ordering as RGBBackend._step.
            np.testing.assert_allclose(self.payload, self.position + self.payload_offset)
            b.og.sim.step_physics()
            self.effects.append(action)
            b.steps += 1
            b._carry_follow()
        b._step = step

        def native(*args, **kwargs):
            self.args.append((args, kwargs))
            try:
                b.og.sim.step_physics()
                self.position[:] = [2., 3., .01]
                yield 'native_settle_1'
                yield 'native_settle_2'
            finally:
                self.generator_closed = True
        b._navigation_primitives = SimpleNamespace(apply_ref=native)
        b._navigate_gt = Mock(side_effect=AssertionError('Unexpected GT fallback'))

    def record(self):
        return json.loads((self.b.output / 'symbolic_navigation.jsonl').read_text().splitlines()[-1])

    def test_native_dispatch_preserves_default_attempts_and_syncs_payload(self):
        result = self.b._navigate(self.target, 2)
        self.assertEqual(self.args, [(('native_navigate_to', self.target), {})])
        self.assertEqual(self.b.steps, 2)
        self.assertEqual(self.b.sampling_physics_steps, 1)
        self.assertTrue(self.generator_closed)
        self.assertIs(self.b.og.sim.step_physics, self.original_physics)
        self.assertFalse(result['path_executed'])
        self.assertEqual(self.record()['status'], 'passed')
        self.assertEqual(self.b.frames_revision, -1)
        np.testing.assert_allclose(self.payload, [2.2, 3., 1.01])

    def test_rgb_point_resolves_object_and_does_not_become_base_coordinate(self):
        pixel = {'image_ref': 'fresh', 'point': [.4, .6]}
        self.b._ground = Mock(return_value=(self.target, [99., 99., 99.], {'private': True}))
        result = RGBBackend.execute_visual.__wrapped__(self.b, 'navigate_to', pixel, 2)
        self.b._ground.assert_called_once_with(pixel, require_object=True, reject_particles=False)
        self.assertIs(self.args[0][0][1], self.target)
        self.assertIn('private_grounding', result)
        np.testing.assert_allclose(self.position, [2., 3., .01])

    def test_native_failure_is_returned_without_gt_fallback_or_rollback(self):
        def failed(*args):
            self.position[:] = [1., 1., .01]
            raise NativeErrorGroup()
            yield
        self.b._navigation_primitives.apply_ref = failed
        with self.assertRaises(SkillError) as raised:
            self.b._navigate(self.target, 100)
        self.assertEqual(raised.exception.code, 'planning_error')
        self.assertTrue(raised.exception.changed)
        np.testing.assert_allclose(self.payload, [1.2, 1., 1.01])
        self.assertEqual(self.record()['status'], 'failed')
        self.assertIs(self.b.og.sim.step_physics, self.original_physics)
        self.b._navigate_gt.assert_not_called()

    def test_zero_budget_never_enters_native_sampler(self):
        with self.assertRaises(SkillError) as raised:
            self.b._navigate(self.target, 0)
        self.assertEqual(raised.exception.code, 'budget_exhausted')
        self.assertFalse(self.args)
        np.testing.assert_allclose(self.position, [0., 0., .01])

    def test_step_budget_closes_native_generator_and_restores_physics_hook(self):
        with self.assertRaises(SkillError) as raised:
            self.b._navigate(self.target, 1)
        self.assertEqual(raised.exception.code, 'action_timeout')
        self.assertEqual(self.b.steps, 1)
        self.assertTrue(self.generator_closed)
        self.assertIs(self.b.og.sim.step_physics, self.original_physics)
        self.assertEqual(self.record()['env_steps'], 1)
        np.testing.assert_allclose(self.payload, [2.2, 3., 1.01])

    def test_cancellation_after_teleport_keeps_contents_at_new_pose(self):
        original = self.b._navigation_primitives.apply_ref
        def canceled(*args):
            generator = original(*args)
            try:
                next(generator)
                raise SkillError('cancelled', 'stop', changed=True)
                yield
            finally:
                generator.close()
        self.b._navigation_primitives.apply_ref = canceled
        with self.assertRaises(SkillError) as raised:
            self.b._navigate(self.target, 100)
        self.assertEqual(raised.exception.code, 'cancelled')
        self.assertEqual(self.b.steps, 0)
        self.assertTrue(self.generator_closed)
        self.assertIs(self.b.og.sim.step_physics, self.original_physics)
        np.testing.assert_allclose(self.payload, [2.2, 3., 1.01])

    def test_real_carry_follow_moves_container_and_rigid_content_before_physics(self):
        b = self.b
        class Body:
            def __init__(self): self.position = np.zeros(3)
            def set_position_orientation(self, position, orientation): self.position = position.copy()
            def keep_still(self): pass
        container, food = Body(), Body()
        b.env = SimpleNamespace(scene=SimpleNamespace(objects=[container, food]))
        b._ideal_held = container
        b._carry_relative = (np.array([.3, 0., 1.]), None)
        b._carry_contents = [(food, (np.array([0., 0., .1]), None))]
        b._carry_follow = RGBBackend._carry_follow.__get__(b)
        transforms = ModuleType('omnigibson.utils.transform_utils')
        transforms.pose_transform = lambda p, q, offset, relative_q: (p + offset, q)
        utils = ModuleType('omnigibson.utils'); utils.transform_utils = transforms
        og = ModuleType('omnigibson'); og.utils = utils
        def step(action):
            b._carry_follow()
            np.testing.assert_allclose(container.position, [2.3, 3., 1.01])
            np.testing.assert_allclose(food.position, [2.3, 3., 1.11])
            b.steps += 1
        b._step = step
        with patch.dict('sys.modules', {'omnigibson': og, 'omnigibson.utils': utils,
                                      'omnigibson.utils.transform_utils': transforms}):
            b._navigate(self.target, 2)
        np.testing.assert_allclose(food.position - container.position, [0., 0., .1])


if __name__ == '__main__':
    unittest.main()
