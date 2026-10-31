"""Review pages must distinguish model runs from scripted executor trials."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


spec = importlib.util.spec_from_file_location('build_review', Path(__file__).resolve().parents[1] / 'scripts/build_review.py')
build_review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_review)


class ReviewMetadataTests(unittest.TestCase):
    def test_saved_decision_fields_are_not_inferred_from_current_profile(self):
        for fields in (['state', 'reason', 'expected'],
                       ['state', 'active_subgoal', 'reason', 'expected']):
            with self.subTest(fields=fields), tempfile.TemporaryDirectory() as root:
                run = Path(root) / 'run'
                run.mkdir()
                controller = Path(root) / 'controller'
                controller.mkdir()
                (controller / 'mcp_preflight.json').write_text(json.dumps({
                    'tool_names': ['initialize'], 'tool_schemas': {
                        'initialize': {'required': ['decision'], 'properties': {
                            'decision': {'required': fields}}}}}))
                configuration = build_review.review_configuration(run, {}, {}, [])
                self.assertEqual(configuration['tools']['decision_fields']['initialize'], fields)

    def test_missing_catalog_is_unknown_even_when_no_planning_tool_was_called(self):
        with tempfile.TemporaryDirectory() as root:
            configuration = build_review.review_configuration(Path(root), {}, {}, [])
        self.assertTrue(configuration['comparison_group'].startswith('未记录'))
        self.assertIsNone(configuration['tools']['enabled'])

    def test_archived_skill_ablation_is_not_relabelled_as_current_without_group(self):
        with tempfile.TemporaryDirectory() as root:
            run = Path(root) / 'run'
            run.mkdir()
            controller = Path(root) / 'controller'
            controller.mkdir()
            (run / 'skill_manifest.json').write_text(json.dumps({'skills': []}))
            (controller / 'mcp_preflight.json').write_text(json.dumps(
                {'tool_names': ['initialize', 'act', 'finish', 'update_plan', 'remember', 'recall']}))
            configuration = build_review.review_configuration(run, {}, {}, [])
        self.assertTrue(configuration['comparison_group'].startswith('其他/历史'))

    def test_scripted_trial_never_inherits_a_model_label(self):
        metadata = build_review.review_metadata(
            {'model': 'stale-model'},
            {'config': {'task': 'clean_a_keyboard', 'instance': 301, 'seed': 0, 'model_used': False}},
            {'model': 'stale-controller-model'})
        self.assertEqual(metadata['task'], 'clean_a_keyboard')
        self.assertEqual(metadata['model'], '未使用模型')
        self.assertEqual(metadata['reasoning_effort'], '不适用')

    def test_manual_public_act_trial_is_labeled_as_manual(self):
        metadata = build_review.review_metadata(
            {}, {'config': {'task': 'sweeping_garage', 'model_used': False,
                            'selection': 'manual RGB pixel, public act interface'}})
        self.assertEqual(metadata['execution'], '人工 RGB 选点，经公开 act 接口')
        self.assertEqual(metadata['model'], '未使用模型')

    def test_controller_model_and_explicit_effort(self):
        metadata = build_review.review_metadata(
            {}, {'config': {'task': 'turning_on_radio', 'instance': 301, 'seed': 0}},
            {'model': 'gpt-6-astra', 'command': ['codex', '-c', 'model_reasoning_effort="high"']})
        self.assertEqual(metadata['model'], 'gpt-6-astra')
        self.assertEqual(metadata['reasoning_effort'], 'high')

    def test_missing_effort_is_not_inferred_from_model_or_directory(self):
        metadata = build_review.review_metadata(
            {'model': 'gpt-6-astra'}, {'config': {'task': 'turning_on_radio'}},
            {'model': 'gpt-6-astra', 'command': ['-c', 'model_reasoning_summary="auto"']})
        self.assertEqual(metadata['reasoning_effort'], '未记录')

    def test_preflight_failure_is_not_labeled_model_driven(self):
        metadata = build_review.review_metadata(
            {}, {'config': {'task': 'clean_a_keyboard'}},
            {'model': 'gpt-6-astra', 'model_reasoning_effort': 'low',
             'failure_stage': 'prepare_project', 'status': 'unsupported'})
        self.assertEqual(metadata['execution'], '模型未启动')
        self.assertEqual(metadata['reasoning_effort'], 'low')


if __name__ == '__main__':
    unittest.main()
