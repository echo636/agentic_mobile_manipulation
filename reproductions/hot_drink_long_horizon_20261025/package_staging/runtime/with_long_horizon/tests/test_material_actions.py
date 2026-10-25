"""Contract checks for RGB-selected tool actions without simulator imports."""
import sys
import types
import unittest
from contextlib import nullcontext
from unittest.mock import patch

from manipulation_agent.contracts import SkillError
from manipulation_agent.executors.material_actions import CheckedMaterialActions, eligible_systems
from manipulation_agent.executors.primitives import PRIMITIVES


class MaterialActionTests(unittest.TestCase):
    def setUp(self):
        self.Covered = type('Covered', (), {})
        self.ParticleRemover = type('ParticleRemover', (), {})
        self.ParticleApplier = type('ParticleApplier', (), {})
        self.Saturated = type('Saturated', (), {})
        self.ParticleSource = type('ParticleSource', (), {})
        self.Contains = type('Contains', (), {})
        states = types.ModuleType('omnigibson.object_states')
        for name in ('Covered', 'ParticleRemover', 'ParticleApplier', 'Saturated',
                     'ParticleSource', 'Contains'):
            setattr(states, name, getattr(self, name))
        methods = types.SimpleNamespace(ADJACENCY=1, PROJECTION=2)
        constants = types.ModuleType('omnigibson.utils.constants')
        constants.ParticleModifyMethod = methods
        self.modules = patch.dict(sys.modules, {
            'omnigibson': types.ModuleType('omnigibson'),
            'omnigibson.object_states': states,
            'omnigibson.utils': types.ModuleType('omnigibson.utils'),
            'omnigibson.utils.constants': constants,
        })
        self.methods = methods
        self.system = types.SimpleNamespace(name='dust', states={})
        self.scene = types.SimpleNamespace(system_registry=types.SimpleNamespace(objects=[self.system]),
                                           get_system=lambda name, force_init=False: self.system)
        self.covered_value = [True]
        self.covered = types.SimpleNamespace(
            get_value=lambda system: self.covered_value[0],
            set_value=lambda system, value: self.covered_value.__setitem__(0, value) or True)
        self.target = types.SimpleNamespace(states={self.Covered: self.covered}, scene=self.scene)
        self.modifier = types.SimpleNamespace(method=self.methods.ADJACENCY,
            supports_system=lambda name: True, check_conditions_for_system=lambda name: True,
            conditions={'dust': []})
        self.tool = types.SimpleNamespace(category='broom', states={self.ParticleRemover: self.modifier})
        sim = types.SimpleNamespace(dump_state=lambda **kw: {}, load_state=lambda *a, **kw: None)
        self.backend = CheckedMaterialActions()
        self.backend.og = types.SimpleNamespace(sim=sim)
        self.backend.robot = types.SimpleNamespace(get_joint_positions=lambda: [], q_to_action=lambda q: q)
        self.backend._get_held = lambda: self.tool
        self.backend._anchored_operation = lambda target: nullcontext()
        self.backend._placement_record = lambda result: None
        self.backend._step = lambda action: None

    def test_all_requested_actions_are_in_public_catalog(self):
        self.assertTrue({'wipe', 'cut', 'soak', 'sweep', 'spray', 'spread', 'hang', 'vacuum'} <= set(PRIMITIVES))

    def test_sweep_removes_only_compatible_selected_material(self):
        with self.modules:
            result = self.backend._checked_surface_action('sweep', self.target, 6)
        self.assertFalse(self.covered_value[0])
        self.assertEqual(result['material_systems'], ['dust'])
        self.assertEqual(result['postcondition'], 'Covered.get_value_after_settling')

    def test_sweep_rejects_wrong_tool_before_state_change(self):
        self.tool.category = 'cloth'
        with self.modules, self.assertRaises(SkillError) as failure:
            self.backend._checked_surface_action('sweep', self.target, 6)
        self.assertEqual(failure.exception.code, 'pre_condition_error')
        self.assertTrue(self.covered_value[0])

    def test_spray_requires_projection_applicator_and_applies_material(self):
        self.tool.category = 'atomizer'
        self.tool.states = {self.ParticleApplier: self.modifier}
        with self.modules, self.assertRaises(SkillError):
            self.backend._checked_surface_action('spray', self.target, 6)
        self.modifier.method = self.methods.PROJECTION
        self.covered_value[0] = False
        with self.modules:
            result = self.backend._checked_surface_action('spray', self.target, 6)
        self.assertTrue(self.covered_value[0])
        self.assertEqual(result['material_systems'], ['dust'])

    def test_ideal_spray_skips_only_physical_overlap_clause(self):
        modifier = types.SimpleNamespace(obj=object(), requires_overlap=True,
            supports_system=lambda name: True,
            check_conditions_for_system=lambda name: False,
            conditions={'dust': [lambda obj: True, lambda obj: True, lambda obj: False]})
        self.assertEqual(eligible_systems([self.system], modifier, ideal_projection=True), [self.system])
        modifier.conditions['dust'][0] = lambda obj: False
        self.assertEqual(eligible_systems([self.system], modifier, ideal_projection=True), [])

    def test_soak_saturates_only_from_active_compatible_source(self):
        self.tool.states[self.Saturated] = types.SimpleNamespace(
            set_value=lambda system, value: setattr(self, 'saturated', value) or True,
            get_value=lambda system: getattr(self, 'saturated', False))
        self.target.states[self.ParticleSource] = types.SimpleNamespace(
            check_conditions_for_system=lambda name: True)
        with self.modules:
            result = self.backend._checked_soak(self.target, 6)
        self.assertTrue(self.saturated)
        self.assertEqual(result['material_systems'], ['dust'])

    def test_spread_requires_material_already_held_by_tool(self):
        self.covered_value[0] = False
        self.tool.states = {self.Saturated: types.SimpleNamespace(get_value=lambda system: False)}
        with self.modules, self.assertRaises(SkillError) as failure:
            self.backend._checked_surface_action('spread', self.target, 6)
        self.assertEqual(failure.exception.code, 'pre_condition_error')
        self.tool.states[self.Saturated] = types.SimpleNamespace(get_value=lambda system: True)
        with self.modules:
            result = self.backend._checked_surface_action('spread', self.target, 6)
        self.assertTrue(self.covered_value[0])
        self.assertEqual(result['material_systems'], ['dust'])

    def test_cut_uses_official_transition_and_checks_replacement(self):
        class SlicingRule:
            def transition(self, candidates):
                self.assertion(candidates)
                return types.SimpleNamespace(add=[object()], remove=[self.target])
        rule = SlicingRule()
        rule.target = self.target
        rule.assertion = lambda candidates: self.assertEqual(candidates, {'sliceable': [self.target]})
        transitions = types.ModuleType('omnigibson.transition_rules')
        transitions.SlicingRule = SlicingRule
        transitions.DicingRule = type('DicingRule', (), {})
        self.tool._abilities = {'slicer'}
        self.target._abilities = {'sliceable'}
        self.scene.objects = [self.target]
        self.scene.transition_rule_api = types.SimpleNamespace(
            active_rules=[rule],
            execute_transition=lambda added_obj_attrs, removed_objs:
                self.scene.objects.remove(self.target))
        self.backend.frames_revision = 0
        with self.modules, patch.dict(sys.modules, {'omnigibson.transition_rules': transitions}):
            result = self.backend._checked_cut(self.target, 6)
        self.assertEqual(result['transition'], 'SlicingRule')
        self.assertEqual(self.backend.frames_revision, -1)
        self.assertNotIn(self.target, self.scene.objects)


if __name__ == '__main__':
    unittest.main()
