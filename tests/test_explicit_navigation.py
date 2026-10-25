"""Manipulation must return control to the agent rather than drive the base."""
import importlib.util
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager, nullcontext
from pathlib import Path
from unittest.mock import patch

from manipulation_agent.contracts import SkillError
from manipulation_agent.executors.omnigibson_rgb import RGBBackend
from manipulation_agent.executors.primitives import PRIMITIVES
from manipulation_agent.observations.boundary import public_execution_error


MANIPULATIONS = set(PRIMITIVES) - {'navigate_to', 'release', 'wait'}
NEEDS_HELD = {'attach', 'hang', 'place_inside', 'place_on_top', 'place_under',
              'place_next_to', 'wipe', 'sweep', 'vacuum', 'spray', 'spread', 'soak', 'cut', 'pour', 'scoop'}


@unittest.skipUnless(importlib.util.find_spec('numpy'), 'Requires numpy')
class ExplicitNavigationTests(unittest.TestCase):
    def setUp(self):
        import numpy as np
        self.np = np
        self.b = RGBBackend.__new__(RGBBackend)
        self.b.torch = types.SimpleNamespace(linalg=np.linalg)
        self.b.ideal_carry = True
        self.b.video = None
        self.b.demo_motion = False
        self.b._demo_arm = None
        self.b.inside_placement = 'official_volume'
        self.b.steps = 0
        self.b._complete_pending_object_initialization = lambda remaining: None
        self.base = np.array([0., 0., .01])
        self.orientation = np.array([0., 0., 0., 1.])
        self.b.robot = types.SimpleNamespace(
            get_position_orientation=lambda: (self.base.copy(), self.orientation.copy()))
        self.point = np.array([2., 0., 1.])
        self.obj = types.SimpleNamespace(name='private_target', states={}, fixed_base=False, category='hook')
        self.tool = object()
        self.held = None
        self.effects = []
        self.b._get_held = lambda: self.held
        self.b._anchored_operation = nullcontext
        self.b._ground = lambda *a, **kw: (self.obj, self.point.copy(), {'object': 'private_target'})
        self.b._navigate = lambda *a, **kw: self.fail('Manipulation attempted implicit navigation')
        self.b._capture_cut_target = lambda obj: None
        for method in ('_ideal_grasp', '_checked_attach', '_checked_surface_action', '_checked_soak',
                       '_checked_cut', '_checked_transfer', '_ideal_state_action', '_checked_place_on_top',
                       '_checked_place_under', '_checked_place_next_to', '_checked_place_inside'):
            def effect(*args, method=method, **kwargs):
                self.effects.append(method)
                self.b.steps += 1
                return {'operation': method}
            setattr(self.b, method, effect)
        states = types.ModuleType('omnigibson.object_states')
        states.Open = type('Open', (), {})
        states.Inside = type('Inside', (), {})
        self.modules = patch.dict(sys.modules, {'omnigibson.object_states': states})
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def run_action(self, primitive):
        self.held = self.tool if primitive in NEEDS_HELD else None
        return RGBBackend.execute_visual.__wrapped__(self.b, primitive, object(), 700)

    def test_every_distant_manipulation_rejects_without_motion_or_effect(self):
        for demo in (False, True):
            self.b.demo_motion = demo
            for primitive in sorted(MANIPULATIONS):
                with self.subTest(primitive=primitive, demo=demo):
                    with self.assertRaises(SkillError) as raised:
                        self.run_action(primitive)
                    error = public_execution_error(raised.exception)
                    self.assertEqual(error['code'], 'out_of_reach')
                    self.assertFalse(error['world_may_have_changed'])
                    self.assertIn('navigate_to', error['message'])
                    self.assertIn('returned RGB', error['message'])
                    self.assertNotIn('private_target', str(error))
                    self.assertEqual(self.b.steps, 0)
                    self.assertEqual(self.effects, [])
                    self.np.testing.assert_array_equal(self.base, [0., 0., .01])
                    self.np.testing.assert_array_equal(self.orientation, [0., 0., 0., 1.])

    def test_near_manipulations_still_dispatch_without_navigation(self):
        self.point[0] = 1.
        for primitive in sorted(MANIPULATIONS):
            with self.subTest(primitive=primitive):
                before = len(self.effects)
                result = self.run_action(primitive)
                self.assertEqual(len(self.effects), before + 1)
                self.assertIn('private_grounding', result)
                self.np.testing.assert_array_equal(self.base, [0., 0., .01])

    def test_demo_arm_failure_does_not_trigger_closer_base_retry(self):
        self.point[0] = 1.
        self.b.demo_motion = True
        reaches = []
        def reach(*args, **kwargs):
            reaches.append(kwargs['action'])
            self.b._demo_last_reach_error = .8
            self.b.steps += 3
            return 3
        self.b._demo_reach = reach
        for primitive in sorted(MANIPULATIONS):
            with self.subTest(primitive=primitive):
                reaches.clear()
                with self.assertRaises(SkillError) as raised:
                    self.run_action(primitive)
                self.assertEqual(raised.exception.code, 'out_of_reach')
                self.assertTrue(raised.exception.changed)  # The arm has moved.
                self.assertEqual(reaches, [primitive])
                self.assertEqual(self.effects, [])
                self.np.testing.assert_array_equal(self.base, [0., 0., .01])

    def test_only_explicit_navigation_dispatches_base_motion(self):
        calls = []
        def navigate(anchor, max_steps):
            calls.append(anchor)
            self.base[:] = self.point
            return {'nav_status': 'reached'}
        self.b._navigate = navigate
        result = self.run_action('navigate_to')
        self.assertEqual(result['nav_status'], 'reached')
        self.assertEqual(len(calls), 1)
        self.np.testing.assert_array_equal(self.base, self.point)
        self.assertEqual(self.effects, [])

    def test_symbolic_fallback_and_release_hold_base_during_execution(self):
        self.point[0] = 1.
        self.b.ideal_carry = False
        anchored = []
        @contextmanager
        def anchor():
            anchored.append(True)
            try:
                yield
            finally:
                anchored.pop()
        self.b._anchored_operation = anchor
        def execute(primitive, target, steps):
            self.assertEqual(anchored, [True])
            return {'primitive': primitive}
        self.b.execute = execute
        for primitive in ('grasp', 'open', 'close', 'toggle_on', 'toggle_off', 'release'):
            with self.subTest(primitive=primitive):
                self.assertEqual(self.run_action(primitive)['primitive'], primitive)
                self.assertEqual(anchored, [])

    def test_cut_holds_base_without_anchoring_a_target_that_can_be_deleted(self):
        self.point[0] = 1.
        anchored = []
        @contextmanager
        def anchor(*objects):
            self.assertEqual(objects, ())
            anchored.append(True)
            try:
                yield
            finally:
                anchored.pop()
        self.b._anchored_operation = anchor
        def cut(target, steps):
            self.assertEqual(anchored, [True])
            return {'primitive': 'cut'}
        self.b._checked_cut = cut
        self.assertEqual(self.run_action('cut')['primitive'], 'cut')
        self.assertEqual(anchored, [])

    def test_harness_returns_reach_hint_and_four_fresh_views_without_physics(self):
        from manipulation_agent.observations.mock_rgb import MockRGBBackend
        from manipulation_agent.records import Recorder
        from manipulation_agent.vision_harness import VisionHarness
        with tempfile.TemporaryDirectory() as folder:
            recorder = Recorder(Path(folder) / 'episode', {'validation': 'cpu_contract_only'})
            self.b.output = recorder.output
            observer = MockRGBBackend(recorder.output)
            self.b.observe = observer.observe
            self.b.provenance = lambda: {'validation': 'cpu_contract_only'}
            harness = VisionHarness(self.b, recorder, profile='minimal')
            old = harness.snapshot
            result = harness.call('act', {'primitive': 'grasp', 'revision': 0,
                'target': {'image_ref': old['images'][0]['image_ref'], 'point': [.5, .5]}}, 'far-grasp')
            self.assertFalse(result['ok'])
            self.assertEqual(result['error']['code'], 'out_of_reach')
            self.assertFalse(result['error']['world_may_have_changed'])
            self.assertIn('navigate_to', result['error']['message'])
            self.assertEqual({i['view'] for i in result['observation']['images']},
                             {'front', 'back', 'left', 'right'})
            self.assertEqual(result['observation']['revision'], 1)
            self.assertNotEqual(result['observation']['images'][0]['image_ref'], old['images'][0]['image_ref'])
            self.assertEqual(self.b.steps, 0)


if __name__ == '__main__':
    unittest.main()
