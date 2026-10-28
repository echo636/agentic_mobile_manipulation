"""Release acceptance must reject incomplete or mismatched evidence."""
import json
from pathlib import Path
import tempfile
import unittest

from manipulation_agent.references.custom_readiness import evidence, validate_release, _physical
from manipulation_agent.tasks.custom_tasks import sha256
from manipulation_agent.tasks.long_horizon import TemporalGoalEvaluator


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = Path(__file__).resolve().parents[1]/'task_designs/evaluation_v2/workshop_setup_and_return_v2.json'
        spec = json.loads(source.read_text())
        # A CPU fixture is intentionally not evidence of physical feasibility.
        scene = self.root/'scene.json'
        scene.write_text('{}')
        spec['frozen_instance'] = {'path': 'scene.json', 'sha256': sha256(scene)}
        self.spec = self.root/'task.json'
        self.spec.write_text(json.dumps(spec))
        self.manifest = self.root/'release.json'
        self.release = {'schema_version': 1, 'official_submission_eligible': False,
                        'benchmark_ready': True, 'source_commit': 'a'*40, 'source_sha256': 'b'*64,
                        'required_task_ids': [spec['id']], 'tasks': [
                            {'task': spec['id'], 'spec': {'path': 'task.json', 'sha256': sha256(self.spec)},
                             'references': [], 'negatives': []}]}

    def validate(self):
        self.manifest.write_text(json.dumps(self.release))
        return validate_release(self.manifest)

    def test_declared_ready_and_valid_contract_are_not_physical_evidence(self):
        result = self.validate()
        self.assertFalse(result['benchmark_ready'])
        checks = result['tasks'][0]['checks']
        self.assertEqual(checks['static_contract'], 'passed')
        self.assertEqual(checks['references'], 'failed')
        self.assertEqual(checks['negatives'], 'failed')
        self.assertEqual(checks['policy_interface'], 'failed')

    def test_changed_public_contract_invalidates_prior_release(self):
        spec = json.loads(self.spec.read_text())
        spec['public_instruction'] = 'A different task'
        self.spec.write_text(json.dumps(spec))
        result = self.validate()
        self.assertFalse(result['benchmark_ready'])
        self.assertIn('contract changed', result['tasks'][0]['errors'][0])

    def test_partial_or_duplicate_task_scope_is_rejected(self):
        self.release['required_task_ids'].append('another_required_task')
        with self.assertRaisesRegex(ValueError, 'scope'):
            self.validate()
        self.release['required_task_ids'].pop()
        self.release['tasks'].append(self.release['tasks'][0])
        with self.assertRaisesRegex(ValueError, 'scope'):
            self.validate()

    def test_replaced_failed_evidence_cannot_reuse_a_passed_hash(self):
        path = self.root/'validation.json'
        path.write_text('{"status":"passed"}')
        ref = {'path': path.name, 'sha256': sha256(path)}
        self.assertEqual(evidence(self.root, ref)['status'], 'passed')
        path.write_text('{"status":"failed"}')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            evidence(self.root, ref)

    def test_missing_physics_artifact_does_not_crash_or_pass_gate(self):
        self.release['tasks'][0]['references'] = [{'validation': {'path': 'absent.json', 'sha256': 'c'*64}}]
        result = self.validate()
        self.assertFalse(result['benchmark_ready'])
        self.assertEqual(result['tasks'][0]['checks']['references'], 'failed')

    def test_batch_preparation_refuses_incomplete_evidence_without_creating_output(self):
        import importlib.util
        path = Path(__file__).resolve().parents[1]/'scripts/prepare_custom_evaluation.py'
        definition = importlib.util.spec_from_file_location('prepare_custom_evaluation', path)
        module = importlib.util.module_from_spec(definition)
        definition.loader.exec_module(module)
        self.validate()
        with self.assertRaisesRegex(ValueError, 'not ready'):
            module.prepare(self.manifest, self.root/'assets.json', self.root/'batch', 'test')
        self.assertFalse((self.root/'batch').exists())

    def test_paired_manifest_changes_only_goal_skill_and_arm_identity(self):
        import importlib.util
        from unittest.mock import patch
        path = Path(__file__).resolve().parents[1]/'scripts/prepare_custom_evaluation.py'
        definition = importlib.util.spec_from_file_location('prepare_custom_evaluation', path)
        module = importlib.util.module_from_spec(definition)
        definition.loader.exec_module(module)
        self.validate()
        spec = json.loads(self.spec.read_text())
        assets = self.root/'assets.json'
        assets.write_text(json.dumps({'tasks': [{'task': spec['id'], 'scene': 'test_fixture',
            'custom_task_spec_sha256': sha256(self.spec), 'custom_instance_sha256': sha256(self.root/'scene.json'),
            'bddl_sha256': 'fixture', 'asset_sha256': {'scene_template': 'fixture'}}]}))
        # Only the manifest builder is under test here; this mock never asserts
        # the CPU fixture is physically ready or launches an experiment.
        with patch.object(module, 'validate_release', return_value={'benchmark_ready': True, 'tasks': []}):
            module.prepare(self.manifest, assets, self.root/'batch', 'fixture')
        arms = [json.loads((self.root/'batch'/arm/'manifest.json').read_text())
                for arm in ('hierarchical', 'unguided')]
        self.assertEqual([m['goal_skill'] for m in arms], ['hierarchical', 'none'])
        for manifest in arms:
            self.assertTrue(manifest['require_task_notes'])
            self.assertEqual(manifest['max_agent_steps'], 200)
            self.assertIsNone(manifest['model_timeout_seconds'])
            self.assertEqual(manifest['model_reasoning_effort'], 'low')
            for field in ('goal_skill', 'comparison_arm', 'batch_title'):
                manifest.pop(field)
            manifest['tasks'][0].pop('run_id')
        self.assertEqual(*arms)

    def physical_fixture(self):
        spec = json.loads(self.spec.read_text())
        spec_hash = sha256(self.spec)
        scene_hash = spec['frozen_instance']['sha256']
        runtime = {'packages': {'simulator': 'cpu-test-fixture'},
                   'assets': {'fixture': 'cpu-test-fixture'}, 'omnigibson_source_commit': 'd'*40}
        self.release['runtime'] = runtime
        self.release['tasks'][0]['asset_sha256'] = {'bddl': 'fixture-bddl'}
        evaluation = {'task_success': True, 'score_type': spec['evaluation']['score_type'],
                      'model_notes_used_for_scoring': False, 'public_constraint_violations': [],
                      'terminal_conditions': dict.fromkeys(spec['evaluation']['terminal'], True),
                      'historical_conditions': dict.fromkeys(spec['evaluation']['milestones'], True),
                      'constraint_conditions': TemporalGoalEvaluator(spec['evaluation']).result(
                          dict.fromkeys(spec['evaluation']['predicates'], False))['constraint_conditions']}
        records = {
            'validation': {'status': 'passed', 'cleanup_status': 'passed', 'task': spec['id'],
                'instance': spec['instance_id'], 'seed': spec['base']['seed'],
                'spec_sha256': spec_hash, 'scene_sha256': scene_hash,
                'source_commit': self.release['source_commit'], 'source_sha256': self.release['source_sha256'],
                'host': 'fixture-host', 'interpreter': 'fixture-python', 'pid': 1,
                'gpu_uuid': 'fixture-gpu', 'unit': 'fixture-unit', 'output_path': '/fixture-output',
                'model_used': False, 'autonomous_success_claimed': False, 'backend': runtime,
                'action_attempts': 10, 'evaluation': evaluation,
                'validation_level': 'real_simulator_checked_executor_privileged_full_reference'},
            'launch': {'status': 'passed', 'exit_code': 0, 'output_path': '/fixture-output', 'unit': 'fixture-unit'},
            'initial': {'status': 'passed', 'input_hashes': {'custom_task_spec': spec_hash,
                       'custom_initial_scene': scene_hash}, 'initial_evaluation': {'task_success': False}},
            'admission': {'status': 'passed', 'host': 'fixture-host', 'gpu_uuid': 'fixture-gpu'},
            'assets': {'status': 'passed', 'tasks': [{'task': spec['id'], 'status': 'passed',
                       'custom_scene_sha256': scene_hash, 'files': {'bddl': {'sha256': 'fixture-bddl'}}}]}}
        bundle = {}
        for name, record in records.items():
            path = self.root/(name+'.json')
            path.write_text(json.dumps(record))
            bundle[name] = {'path': path.name, 'sha256': sha256(path)}
        return spec, records, bundle

    def test_constructed_cohort_preserves_600_call_budget_and_schema(self):
        import importlib.util
        from unittest.mock import patch
        repo = Path(__file__).resolve().parents[1]
        definition = importlib.util.spec_from_file_location(
            'prepare_constructed_evaluation', repo/'scripts/prepare_custom_evaluation.py')
        module = importlib.util.module_from_spec(definition)
        definition.loader.exec_module(module)
        spec = json.loads((repo/'task_designs/constructed_100plus/'
                           'garden_treatment_and_distribution_100_v1.json').read_text())
        spec['frozen_instance'] = {'path': 'scene.json', 'sha256': sha256(self.root/'scene.json')}
        self.spec.write_text(json.dumps(spec))
        self.release['tasks'][0].update(task=spec['id'],
            spec={'path': 'task.json', 'sha256': sha256(self.spec)})
        self.release['required_task_ids'] = [spec['id']]
        self.manifest.write_text(json.dumps(self.release))
        assets = self.root/'assets.json'
        assets.write_text(json.dumps({'tasks': [{'task': spec['id'], 'scene': 'cpu_fixture',
            'custom_task_spec_sha256': sha256(self.spec),
            'custom_instance_sha256': sha256(self.root/'scene.json'),
            'bddl_sha256': 'fixture', 'asset_sha256': {}}]}))
        # Only manifest construction is tested; CPU fixtures remain non-physical.
        with patch.object(module, 'validate_release', return_value={'benchmark_ready': True, 'tasks': []}):
            module.prepare(self.manifest, assets, self.root/'batch', 'constructed_fixture')
        for arm in ('hierarchical', 'unguided'):
            manifest = json.loads((self.root/'batch'/arm/'manifest.json').read_text())
            self.assertEqual(manifest['max_agent_steps'], 600)
            self.assertEqual(manifest['max_actions'], 600)
            self.assertEqual(manifest['benchmark'], 'custom_long_horizon_v3')
            self.assertEqual(manifest['tasks'][0]['split'], 'custom_long_horizon_v3_controlled')
            self.assertEqual(manifest['scope'], '1 frozen custom instances; no generalization claim')

    def test_reference_identity_and_complete_score_are_required(self):
        spec, records, bundle = self.physical_fixture()
        accepted = _physical(self.root, bundle, spec, sha256(self.spec), self.release)
        self.assertTrue(accepted['evaluation']['task_success'])
        records['validation']['evaluation']['historical_conditions'] = {}
        path = self.root/'validation.json'
        path.write_text(json.dumps(records['validation']))
        bundle['validation']['sha256'] = sha256(path)
        with self.assertRaisesRegex(ValueError, 'Incomplete independent scoring'):
            _physical(self.root, bundle, spec, sha256(self.spec), self.release)

    def test_new_source_or_dependency_cannot_reuse_old_physics_acceptance(self):
        spec, _, bundle = self.physical_fixture()
        self.release['source_commit'] = 'e'*40
        with self.assertRaisesRegex(ValueError, 'identity/version'):
            _physical(self.root, bundle, spec, sha256(self.spec), self.release)
        self.release['source_commit'] = 'a'*40
        self.release['runtime']['packages'] = {'simulator': 'different-runtime'}
        with self.assertRaisesRegex(ValueError, 'runtime changed'):
            _physical(self.root, bundle, spec, sha256(self.spec), self.release)

    def test_study_transition_guard_is_required_in_complete_scoring(self):
        source = Path(__file__).resolve().parents[1]/'task_designs/complete_v2/study_guest_room_and_laundry_v2.json'
        task = json.loads(source.read_text())
        task['frozen_instance'] = {'path': 'scene.json', 'sha256': sha256(self.root/'scene.json')}
        self.spec.write_text(json.dumps(task))
        self.release['tasks'][0]['task'] = task['id']
        spec, records, bundle = self.physical_fixture()
        guard = 'monitor_off_when_cleaned'
        self.assertTrue(records['validation']['evaluation']['constraint_conditions'][guard])
        _physical(self.root, bundle, spec, sha256(self.spec), self.release)
        original = dict(records['validation']['evaluation']['constraint_conditions'])
        for outcome in ('missing', 'violated'):
            with self.subTest(outcome=outcome):
                constraints = dict(original)
                if outcome == 'missing':
                    constraints.pop(guard)
                else:
                    constraints[guard] = False
                records['validation']['evaluation']['constraint_conditions'] = constraints
                path = self.root/'validation.json'
                path.write_text(json.dumps(records['validation']))
                bundle['validation']['sha256'] = sha256(path)
                with self.assertRaisesRegex(ValueError, 'Incomplete independent scoring: constraint_conditions'):
                    _physical(self.root, bundle, spec, sha256(self.spec), self.release)

    def test_declared_asset_pass_does_not_hide_changed_bddl(self):
        spec, records, bundle = self.physical_fixture()
        records['assets']['tasks'][0]['files']['bddl']['sha256'] = 'changed-bddl'
        path = self.root/'assets.json'
        path.write_text(json.dumps(records['assets']))
        bundle['assets']['sha256'] = sha256(path)
        with self.assertRaisesRegex(ValueError, 'changed pinned'):
            _physical(self.root, bundle, spec, sha256(self.spec), self.release)


if __name__ == '__main__':
    unittest.main()
