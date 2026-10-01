import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('comparison', Path(__file__).resolve().parents[1] / 'scripts/batch_comparison.py')
comparison = importlib.util.module_from_spec(spec)
spec.loader.exec_module(comparison)


class ComparisonTests(unittest.TestCase):
    def rows(self):
        row = {'index': 0, 'task': 'radio', 'name': 'Radio', 'instruction': 'Turn on radio',
               'instance': 301, 'seed': 0, 'instruction_sha256': 'same', 'bddl_sha256': 'same',
               'status': 'planned'}
        return {arm: {'tasks': [copy.deepcopy(row)]} for arm in comparison.ARMS}

    def test_pending_and_unscored_are_not_goal_failures(self):
        rows = self.rows()
        rows['original']['tasks'][0].update(status='failed', task_success=True)
        rows['official']['tasks'][0].update(status='failed', task_success=None)
        result = comparison.summarize_comparison(rows)
        self.assertEqual(result['arms']['original']['official_goal_successes'], 1)
        self.assertEqual(result['arms']['original']['fully_validated_successes'], 0)
        self.assertEqual(result['arms']['official']['unscored_ended'], 1)
        self.assertEqual(result['arms']['motor']['ended'], 0)
        self.assertEqual(result['paired_scored_tasks'], 0)

    def test_paired_subset_requires_all_three_final_scores(self):
        rows = self.rows()
        for arm in comparison.ARMS:
            rows[arm]['tasks'][0].update(status='failed', task_success=arm == 'official')
        result = comparison.summarize_comparison(rows)
        self.assertEqual(result['paired_scored_tasks'], 1)
        self.assertEqual(result['paired_goal_successes'], {'original': 0, 'motor': 0, 'official': 1})

    def test_changed_instruction_or_missing_task_rejected(self):
        rows = self.rows(); rows['motor']['tasks'][0]['instruction_sha256'] = 'hint-added'
        with self.assertRaises(ValueError): comparison.summarize_comparison(rows)
        rows = self.rows(); rows['official']['tasks'] = []
        with self.assertRaises(ValueError): comparison.summarize_comparison(rows)


if __name__ == '__main__': unittest.main()
