"""Review pages must distinguish model runs from scripted executor trials."""
import importlib.util
import unittest
from pathlib import Path


spec = importlib.util.spec_from_file_location('build_review', Path(__file__).resolve().parents[1] / 'scripts/build_review.py')
build_review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_review)


class ReviewMetadataTests(unittest.TestCase):
    def test_custom_terminal_success_is_not_hidden_by_null_official_score(self):
        score, label, success = build_review.review_score({'task_success': True, 'evaluation': {
            'protocol': 'rgb_agent_custom_native_bddl_additional_terminal_conditions_v1',
            'official_task_success': None, 'task_success': True, 'goal_satisfaction_fraction': 1.0,
            'bddl_task_success': True, 'additional_terminal_conditions': {'hand_empty': True}}})
        self.assertIs(success, True)
        self.assertEqual(score, 1.0)
        self.assertIn('显式终态', label)
        self.assertIn('非官方', label)

    def test_satisfied_bddl_does_not_override_failed_terminal_condition(self):
        score, _, success = build_review.review_score({'task_success': False, 'evaluation': {
            'protocol': 'rgb_agent_custom_native_bddl_additional_terminal_conditions_v1',
            'official_task_success': None, 'task_success': False, 'goal_satisfaction_fraction': 1.0,
            'bddl_task_success': True, 'additional_terminal_conditions': {'hand_empty': False}}})
        self.assertEqual(score, 1.0)
        self.assertIs(success, False)

    def test_original_native_custom_protocol_retains_its_score(self):
        score, label, success = build_review.review_score({'task_success': True, 'evaluation': {
            'protocol': 'rgb_agent_custom_native_bddl_sampled_instance',
            'official_task_success': None, 'task_success': True, 'goal_satisfaction_fraction': 1.0}})
        self.assertEqual((score, success), (1.0, True))
        self.assertIn('非官方', label)

    def test_official_evaluation_keeps_zero_score_and_explicit_failure(self):
        score, label, success = build_review.review_score({'task_success': True, 'evaluation': {
            'official_task_success': False, 'official_metrics': {'q_score': {'final': 0}}}})
        self.assertEqual(score, 0)
        self.assertIs(success, False)
        self.assertEqual(label, '本地官方指标 Q')

    def test_lineage_failure_remains_failed_with_all_bddl_goals_satisfied(self):
        score, label, success = build_review.review_score({'task_success': False, 'evaluation': {
            'protocol': 'rgb_agent_custom_native_bddl_lineage_returns_v1',
            'task_success': False, 'official_task_success': None,
            'goal_satisfaction_fraction': 1.0,
            'relational_terminal_conditions': {'half_a:correct_order_inside': False}}})
        self.assertEqual(score, 1.0)
        self.assertFalse(success)
        self.assertIn('材料来源', label)

    def test_scripted_reference_uses_nested_native_score_and_full_success(self):
        score, label, success = build_review.review_score({'task_success': False, 'evaluation': {
            'full_task_success': False, 'native_evaluation': {
                'protocol': 'rgb_agent_custom_native_bddl_lineage_returns_v1',
                'task_success': True, 'goal_satisfaction_fraction': 1.0}}})
        self.assertEqual(score, 1.0)
        self.assertFalse(success)
        self.assertIn('材料来源', label)

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
