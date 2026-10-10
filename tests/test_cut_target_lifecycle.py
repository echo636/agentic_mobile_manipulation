"""Regressions for an official cut that happens during visual-action approach.

Run the real execute_visual routing with tiny scene/step fixtures. A removed
target throws if dereferenced, reproducing the deleted-prim failure boundary.
"""
import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from manipulation_agent.contracts import SkillError


class Target:
    def __getattribute__(self, name):
        if name not in {'deleted', '__dict__', '__class__'} and object.__getattribute__(self, 'deleted'):
            raise RuntimeError('The target prim was deleted: ' + name)
        return object.__getattribute__(self, name)


class Scene:
    def __init__(self):
        self.objects = []

    def object_registry(self, key, name, default=None):
        assert key == 'name'
        return next((obj for obj in self.objects if obj.name == name), default)


@unittest.skipUnless(importlib.util.find_spec('numpy'), 'Requires numpy for visual point fixture')
class CutTargetLifecycle(unittest.TestCase):
    def setUp(self):
        import numpy as np
        from manipulation_agent.executors.omnigibson_rgb import RGBBackend
        class Point(np.ndarray):
            def clone(self):
                return self.copy()
        self.route = RGBBackend.execute_visual.__wrapped__
        self.scene = Scene()
        self.target = Target()
        self.target.deleted = False
        self.target.name = 'log_177'
        self.target.scene = self.scene
        self.target.states = {}
        self.target._abilities = {'sliceable'}
        # Deliberately identical models: the two output instances must not be
        # collapsed by the asset helper's set of category/model pairs.
        self.target.metadata = {'object_parts': {
            '0': {'category': 'half_log', 'model': 'cut_part'},
            '1': {'category': 'half_log', 'model': 'cut_part'}}}
        self.scene.objects = [self.target]
        self.tool = types.SimpleNamespace(_abilities={'slicer'})
        self.backend = RGBBackend.__new__(RGBBackend)
        self.backend.torch = types.SimpleNamespace(linalg=np.linalg)
        self.backend.steps = 0
        self.backend.frames_revision = 7
        self.backend.ideal_carry = True
        self.backend.demo_motion = False
        self.backend._demo_arm = 'left'
        self.backend._get_held = lambda: self.tool
        self.point = np.array([2., 0., .1]).view(Point)
        self.base = np.zeros(3)
        self.backend.robot = types.SimpleNamespace(
            get_position_orientation=lambda: (self.base.copy(), None),
            get_joint_positions=lambda: [], q_to_action=lambda q: q)
        self.backend._ground = lambda *args, **kwargs: (self.target, self.point.copy(), {'fixture': True})
        self.records = []
        self.backend._placement_record = self.records.append
        self.backend._demo_record = lambda **kwargs: None
        self.backend._step = lambda action: setattr(self.backend, 'steps', self.backend.steps+1)
        self.stage = None
        self.replacement = 'valid'
        self.demo_calls = 0
        self.manual_transition_calls = 0

        def navigate(*args, **kwargs):
            self.backend.steps += 1
            self.base[:] = self.point
            if self.stage in {'implicit_approach', 'demo_approach_retry'}:
                self.replace_target()
        self.backend._navigate = navigate

        def reach(point, max_steps, *, anchor, arm, action):
            self.assertIsNone(anchor, 'Cut must not pin a target that a rule can delete')
            self.demo_calls += 1
            self.backend.steps += 1
            stage = ('demo_stroke' if action.endswith('_stroke') else
                     'demo_reach_retry' if action.endswith('_after_approach') else 'demo_reach')
            self.backend._demo_last_reach_error = (
                .2 if stage == 'demo_reach' and self.stage in {'demo_approach_retry', 'demo_reach_retry'} else 0.)
            if self.stage == stage:
                self.replace_target()
            return 1
        self.backend._demo_reach = reach

        test = self
        class SlicingRule:
            def transition(self, candidates):
                test.assertIs(candidates['sliceable'][0], test.target)
                # This access must happen only on the unchanged/manual path.
                name = test.target.name
                test.manual_transition_calls += 1
                return types.SimpleNamespace(add=test.parts(name), remove=[test.target])
        self.scene.transition_rule_api = types.SimpleNamespace(
            active_rules=[SlicingRule()],
            execute_transition=lambda added_obj_attrs, removed_objs: self.remove_and_add(added_obj_attrs))
        states = types.ModuleType('omnigibson.object_states')
        states.Open = type('Open', (), {})
        states.Inside = type('Inside', (), {})
        transitions = types.ModuleType('omnigibson.transition_rules')
        transitions.SlicingRule = SlicingRule
        transitions.DicingRule = type('DicingRule', (), {})
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        asset_root = Path(self.tmp.name)
        usd = asset_root/'objects/half_log/cut_part/usd/cut_part.encrypted.usd'
        usd.parent.mkdir(parents=True)
        usd.write_bytes(b'fixture')
        asset_utils = types.ModuleType('omnigibson.utils.asset_utils')
        asset_utils.get_dataset_path = lambda name: str(asset_root)
        self.modules = patch.dict(sys.modules, {'omnigibson.object_states': states,
            'omnigibson.transition_rules': transitions, 'omnigibson.utils.asset_utils': asset_utils})

    def parts(self, name='log_177'):
        return [types.SimpleNamespace(name=f'half_{name}_{i}', category='half_log', model='cut_part')
                for i in range(2)]

    def remove_and_add(self, parts):
        self.scene.objects = [obj for obj in self.scene.objects if obj is not self.target] + list(parts)
        self.target.deleted = True

    def replace_target(self):
        parts = self.parts()
        if self.replacement == 'unknown':
            parts = []
        elif self.replacement == 'partial':
            parts = parts[:1]
        elif self.replacement == 'wrong_model':
            parts[1].model = 'unrelated_model'
        self.remove_and_add(parts)

    def test_implicit_approach_recognizes_both_new_halves_without_dead_target_read(self):
        self.stage = 'implicit_approach'
        with self.modules:
            result = self.route(self.backend, 'cut', object(), 700)
        self.assertEqual(result['created_objects'], 2)
        self.assertEqual(result['completed_during'], 'implicit_approach')
        self.assertEqual(result['transition'], 'SlicingRule')
        self.assertTrue(result['replacement_verified'])
        self.assertEqual(self.manual_transition_calls, 0)
        self.assertEqual(self.backend.frames_revision, -1)

    def test_unknown_disappearance_is_recoverable_failure_and_invalidates_rgb(self):
        self.stage = 'implicit_approach'
        self.replacement = 'unknown'
        with self.modules, self.assertRaises(SkillError) as failure:
            self.route(self.backend, 'cut', object(), 700)
        self.assertEqual(failure.exception.code, 'target_changed')
        self.assertTrue(failure.exception.changed)
        self.assertEqual(self.backend.frames_revision, -1)
        self.assertIs(self.backend._get_held(), self.tool)
        self.assertEqual(self.manual_transition_calls, 0)
        from manipulation_agent.observations.boundary import public_execution_error
        public = public_execution_error(failure.exception)
        self.assertEqual(public['code'], 'target_changed')
        self.assertTrue(public['world_may_have_changed'])
        self.assertNotIn('log_177', public['message'])

    def test_partial_or_wrong_model_replacements_do_not_count_as_cut_success(self):
        for replacement in ('partial', 'wrong_model'):
            with self.subTest(replacement=replacement):
                self.setUp()
                self.stage = 'implicit_approach'
                self.replacement = replacement
                with self.modules, self.assertRaises(SkillError) as failure:
                    self.route(self.backend, 'cut', object(), 700)
                self.assertEqual(failure.exception.code, 'target_changed')

    def test_preexisting_matching_names_do_not_prove_new_cut(self):
        self.scene.objects += self.parts()
        self.stage = 'implicit_approach'
        with self.modules, self.assertRaises(SkillError) as failure:
            self.route(self.backend, 'cut', object(), 700)
        self.assertEqual(failure.exception.code, 'target_changed')

    def test_unchanged_target_runs_existing_official_manual_transition_once(self):
        with self.modules:
            result = self.route(self.backend, 'cut', object(), 700)
        self.assertEqual(result['transition'], 'SlicingRule')
        self.assertEqual(result['created_objects'], 2)
        self.assertNotIn('completed_during', result)
        self.assertEqual(self.manual_transition_calls, 1)
        self.assertEqual(self.backend.frames_revision, -1)

    def test_each_demo_boundary_resolves_cut_before_reusing_target(self):
        for stage in ('demo_reach', 'demo_approach_retry', 'demo_reach_retry', 'demo_stroke'):
            with self.subTest(stage=stage):
                self.setUp()
                self.base[:] = self.point
                self.backend.demo_motion = True
                self.stage = stage
                with self.modules:
                    result = self.route(self.backend, 'cut', object(), 700)
                self.assertEqual(result['completed_during'], stage)
                self.assertEqual(self.manual_transition_calls, 0)
                self.assertGreater(result['demo_motion']['real_env_steps'], 0)


if __name__ == '__main__':
    unittest.main()
