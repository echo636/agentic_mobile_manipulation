"""Reject misleading design metadata and invalid schedules, without simulating physics."""
import copy
import json
from pathlib import Path
import runpy
import unittest

ROOT = Path(__file__).resolve().parents[1]
audit = runpy.run_path(str(ROOT / 'scripts/audit_nonrepetitive_designs.py'))['audit']


class RedesignAuditTests(unittest.TestCase):
    def setUp(self):
        self.catalog = json.loads((ROOT / 'task_designs/nonrepetitive_v1/catalog.json').read_text())

    def test_catalog_preserves_one_baseline_without_readiness_claim(self):
        result = audit(self.catalog)
        self.assertEqual(result['retained_repetition_baselines'], 1)
        self.assertEqual(len(result['redesigned_tasks']), 4)
        self.assertFalse(result['benchmark_ready'])

    def test_terminal_state_does_not_turn_design_into_physical_evidence(self):
        self.catalog['tasks'][0]['validation']['physical_references'] = 'passed'
        with self.assertRaisesRegex(ValueError, 'run passes'):
            audit(self.catalog)

    def test_same_source_cannot_replace_two_tasks(self):
        self.catalog['tasks'][1]['replaces'] = self.catalog['tasks'][0]['replaces']
        with self.assertRaisesRegex(ValueError, 'replacements'):
            audit(self.catalog)

    def test_reordered_schedule_cannot_skip_a_real_prerequisite(self):
        phases = self.catalog['tasks'][0]['reference_orders'][0]['phases']
        phases[0], phases[1] = phases[1], phases[0]
        with self.assertRaisesRegex(ValueError, 'prerequisite'):
            audit(self.catalog)

    def test_reference_only_cycle_still_invalidates_the_reference(self):
        self.catalog['tasks'][0]['phases'][0]['requires'].append(
            dict(phase='complete', reason_zh='invalid cycle', scope='reference_only'))
        with self.assertRaisesRegex(ValueError, 'Cycle'):
            audit(self.catalog)

    def test_new_action_name_cannot_imply_an_executor_capability(self):
        self.catalog['tasks'][0]['phases'][0]['primitives'].append('repair_device')
        with self.assertRaisesRegex(ValueError, 'Unknown primitive'):
            audit(self.catalog)

    def test_edited_reuse_source_cannot_keep_old_provenance(self):
        self.catalog['sources'][0]['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'Source hash'):
            audit(self.catalog)

    def test_renaming_one_order_does_not_create_three_strategies(self):
        orders = self.catalog['tasks'][0]['reference_orders']
        for row in orders[1:]:
            row['phases'] = copy.deepcopy(orders[0]['phases'])
        with self.assertRaisesRegex(ValueError, 'relabel identical work'):
            audit(self.catalog)


if __name__ == '__main__':
    unittest.main()
