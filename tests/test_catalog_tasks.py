"""A sampled development instance must retain matching preparation evidence."""
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

from manipulation_agent.tasks.catalog_tasks import load_catalog_spec


class CatalogEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.scene = self.root / 'scene.json'
        self.scene.write_text('{"objects_info": {"init_info": {}}}')
        self.sample = self.root / 'sample.json'
        self.evidence = dict(status='passed', model_used=False, task='making_a_meal',
            scene='kitchen', seed=0, bddl_sha256='a' * 64, initial_conditions=[True],
            initial_goal_satisfied=False, saved_files={'scene.json': self.sha(self.scene)})
        self.spec = dict(schema_version=1, kind='behavior1k_sampled_instance',
            task='making_a_meal', scene='kitchen', seed=0, instance_id='sample0',
            bddl_sha256='a' * 64, public_instruction='Serve the cooked meal in a bowl.',
            frozen_instance=dict(path='scene.json', sha256=self.sha(self.scene)),
            environment_config=dict(task=dict(activity_name='making_a_meal'),
                                    scene=dict(scene_model='kitchen')))
        self.path = self.root / 'spec.json'
        self.save()

    @staticmethod
    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def save(self):
        self.sample.write_text(json.dumps(self.evidence))
        self.spec['sampling_record'] = dict(path='sample.json', sha256=self.sha(self.sample))
        self.path.write_text(json.dumps(self.spec))

    def test_valid_evidence_and_relocation_then_changed_scene(self):
        self.assertEqual(load_catalog_spec(self.path)['task'], 'making_a_meal')
        for key in ('frozen_instance', 'sampling_record'):
            self.spec[key]['path'] = str(self.root / self.spec[key]['path'])
        self.path.write_text(json.dumps(self.spec))
        self.assertEqual(load_catalog_spec(self.path)['seed'], 0)
        self.scene.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            load_catalog_spec(self.path)

    def test_reject_failed_or_different_preparation(self):
        for change in (dict(status='failed'), dict(seed=1), dict(initial_conditions=[False]),
                       dict(initial_goal_satisfied=True), dict(saved_files={})):
            with self.subTest(change=change):
                original = dict(self.evidence)
                self.evidence.update(change)
                self.save()
                with self.assertRaisesRegex(ValueError, 'sampling evidence'):
                    load_catalog_spec(self.path)
                self.evidence = original

    def test_custom_native_definition_must_match_sampling_and_file(self):
        definition = self.root / 'problem0.bddl'
        definition.write_text('(define (problem making_a_meal-0))')
        sha = self.sha(definition)
        self.spec.update(kind='custom_bddl_sampled_instance', bddl_sha256=sha,
                         definition={'path': definition.name, 'sha256': sha},
                         use_gpu_dynamics=False)
        self.evidence['bddl_sha256'] = sha
        self.save()
        self.assertEqual(load_catalog_spec(self.path)['kind'], 'custom_bddl_sampled_instance')
        definition.write_text('changed')
        with self.assertRaisesRegex(ValueError, 'BDDL hash mismatch'):
            load_catalog_spec(self.path)

    def test_dynamics_mode_cannot_be_a_truthy_string(self):
        self.spec['use_gpu_dynamics'] = 'false'
        self.save()
        with self.assertRaisesRegex(ValueError, 'explicit boolean'):
            load_catalog_spec(self.path)

    def test_reject_environment_for_another_task(self):
        self.spec['environment_config']['task']['activity_name'] = 'make_pizza'
        self.save()
        with self.assertRaisesRegex(ValueError, 'environment does not match'):
            load_catalog_spec(self.path)

    def test_public_empty_hand_condition_is_custom_only_and_explicit(self):
        self.spec['additional_terminal_conditions'] = ['hand_empty']
        self.save()
        with self.assertRaisesRegex(ValueError, 'require a custom task'):
            load_catalog_spec(self.path)
        definition = self.root / 'problem0.bddl'
        definition.write_text('(define (problem making_a_meal-0))')
        self.spec.update(kind='custom_bddl_sampled_instance', bddl_sha256=self.sha(definition),
                         definition=dict(path=definition.name, sha256=self.sha(definition)))
        self.evidence['bddl_sha256'] = self.sha(definition)
        self.save()
        self.assertEqual(load_catalog_spec(self.path)['additional_terminal_conditions'], ['hand_empty'])
        for invalid in [['task_success'], ['hand_empty', 'hand_empty'], 'hand_empty', [False]]:
            with self.subTest(invalid=invalid):
                self.spec['additional_terminal_conditions'] = invalid
                self.save()
                with self.assertRaisesRegex(ValueError, 'Unsupported additional'):
                    load_catalog_spec(self.path)


class CatalogTerminalScoringTests(unittest.TestCase):
    def backend(self, goals, held, conditions):
        from manipulation_agent.executors.catalog_task import CatalogRGBBackend
        backend = CatalogRGBBackend.__new__(CatalogRGBBackend)
        backend.catalog_spec = dict(kind='custom_bddl_sampled_instance', additional_terminal_conditions=conditions)
        backend._goal_options = lambda: goals
        backend._get_held = lambda: held
        backend.initial_goals = [[False]]
        backend.task_metadata = {'task': 'custom_probe'}
        backend.inside_placement = 'official_volume'
        backend.sampling_physics_steps = 0
        backend.navigation_distance = 0.0
        # Native success comes from the compiled BDDL goal, as in the executor.
        native = any(bool(option) and all(option) for option in goals)
        backend.env = SimpleNamespace(task=SimpleNamespace(success=True, _evaluate_predicate=None,
            ground_goal_state_options=goals,
            compiled_task=SimpleNamespace(check_goal=lambda evaluate: (native, None))))
        return backend

    def test_native_goals_do_not_override_declared_empty_hand(self):
        for goals, held, expected in [([[True, True]], object(), False),
                                      ([[True, False]], None, False),
                                      ([[True, True]], None, True)]:
            with self.subTest(goals=goals, held=held):
                result = self.backend(goals, held, ['hand_empty']).evaluate()
                self.assertEqual(result['task_success'], expected)
                self.assertEqual(result['bddl_task_success'], all(goals[0]))
                self.assertEqual(result['additional_terminal_conditions'], {'hand_empty': held is None})
                self.assertIsNone(result['official_task_success'])

    def test_absent_extension_preserves_bddl_only_contract(self):
        backend = self.backend([[True]], object(), [])
        def unexpected_hand_read():
            raise AssertionError('Legacy scoring must not add a new condition')
        backend._get_held = unexpected_hand_read
        result = backend.evaluate()
        self.assertTrue(result['task_success'])
        self.assertNotIn('additional_terminal_conditions', result)
        self.assertEqual(result['protocol'], 'rgb_agent_custom_native_bddl_sampled_instance')


class CuttingSupportTests(unittest.TestCase):
    def test_reject_unsupported_cut_before_material_transition(self):
        from manipulation_agent.executors.catalog_task import CatalogRGBBackend, RGBBackend
        from manipulation_agent.contracts import SkillError
        marker = object()
        with tempfile.TemporaryDirectory() as directory:
            for declared, supported in [(True, True), (True, False), (False, True)]:
                with self.subTest(declared=declared, supported=supported):
                    target = SimpleNamespace(name='raw', states={marker: SimpleNamespace(get_value=lambda _: supported)})
                    support = SimpleNamespace(name='bench')
                    backend = CatalogRGBBackend.__new__(CatalogRGBBackend)
                    backend.output, backend.steps = Path(directory), 0
                    backend.catalog_spec = {'cutting_support': {'support_binding': 'bench', 'target_bindings': ['raw']}}
                    backend.env = SimpleNamespace(task=SimpleNamespace(object_scope={
                        'bench': support, 'raw': target if declared else object()}))
                    transition = Mock(return_value={'primitive': 'cut'})
                    with patch.dict('sys.modules', {'omnigibson.object_states': SimpleNamespace(OnTop=marker)}), \
                            patch.object(RGBBackend, '_checked_cut', transition):
                        if declared and supported:
                            self.assertEqual(backend._checked_cut(target, 20), {'primitive': 'cut'})
                            transition.assert_called_once_with(target, 20)
                        else:
                            with self.assertRaises(SkillError):
                                backend._checked_cut(target, 20)
                            transition.assert_not_called()


if __name__ == '__main__':
    unittest.main()
