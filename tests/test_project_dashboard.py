import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('project_overview', Path(__file__).parents[1] / 'scripts/project_dashboard.py')
overview = importlib.util.module_from_spec(spec); spec.loader.exec_module(overview)


class ProjectOverviewTests(unittest.TestCase):
    def test_pending_archive_score_is_separate_from_completed_success_count(self):
        stats = overview.counts([
            {'status': 'running', 'stage': 'archiving', 'task_success': True},
            {'status': 'failed', 'task_success': True},  # Task success, evidence failure.
            {'status': 'failed', 'task_success': None, 'io_fault': True},
            {'status': 'planned'},
        ])
        self.assertEqual(stats, {'total': 4, 'completed': 2, 'successes': 1, 'scored': 1,
                                 'unscored': 1, 'io_faults': 1, 'running': 1, 'planned': 1})

    def test_read_failure_retains_previous_snapshot_and_reports_it(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'progress.json'; path.write_text('{"tasks":[1]}')
            reader = overview.Overview(folder)
            self.assertEqual(reader.read(path)['tasks'], [1])
            path.write_text('{incomplete')
            self.assertEqual(reader.read(path)['tasks'], [1])
            self.assertTrue(reader.warnings[-1]['retained_previous'])
            self.assertIsNone(reader.read(Path(folder) / 'unknown.json'))

    def test_actual_run_source_and_audited_io_survive_later_failure_field_changes(self):
        row = {'run_id': 'old', 'status': 'failed', 'run_source': {'commit': 'before'},
               'source': {'commit': 'fixed'}, 'failure': None, 'replay_url': 'manipulation_runs/old/replay.html'}
        result = overview.compact_row(row, '/cohort/hierarchical/', {'old'}, {'fixed'})
        self.assertTrue(result['io_fault'])
        self.assertFalse(result['io_protected'])
        self.assertEqual(result['replay_url'], '/cohort/hierarchical/manipulation_runs/old/replay.html')
        row.update(status='planned')
        self.assertIsNone(overview.compact_row(row, '/', set(), {'fixed'})['source_commit'])

    def test_same_task_in_three_cohorts_keeps_three_denominators(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for key, _, relative, _, _ in overview.SPECS:
                for arm in overview.ARMS:
                    directory = root / 'operations' / relative / arm; directory.mkdir(parents=True)
                    (directory / 'progress.json').write_text(json.dumps({'tasks': [
                        {'index': 4, 'task': 'can_meat', 'run_id': key + arm,
                         'status': 'passed' if key == 'placement' else 'failed',
                         'task_success': key == 'placement'}]}))
            result = overview.Overview(root).build()
            self.assertEqual(len(result['cohorts']), 3)
            self.assertEqual([c['arms']['hierarchical']['summary']['successes'] for c in result['cohorts']], [0, 1, 0])
            self.assertTrue(all(c['arms']['hierarchical']['summary']['total'] == 1 for c in result['cohorts']))
            self.assertTrue(all(a['status'] == 'planned' for a in result['custom_autonomous'].values()))
            self.assertNotIn('overall_success_rate', result)


if __name__ == '__main__':
    unittest.main()
