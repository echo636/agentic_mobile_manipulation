"""Prevent count padding and overclaiming in the extended, unbuilt designs."""
import copy
import json
from pathlib import Path
import runpy
import unittest

ROOT = Path(__file__).resolve().parents[1]
audit = runpy.run_path(str(ROOT/'scripts/audit_long_workflow_designs.py'))['audit']


class ExtendedDesignTests(unittest.TestCase):
    def setUp(self):
        self.catalog = json.loads((ROOT/'task_designs/nonrepetitive_v2/catalog.json').read_text())

    def test_extended_accounting_remains_unbuilt_and_unmeasured(self):
        result = audit(self.catalog)
        self.assertEqual([r['conditional_reference_operations'] for r in result['operation_ledgers']],
                         [108, 101, 104, 101])
        self.assertIsNone(result['measured_lengths'])
        self.assertFalse(result['benchmark_ready'])

    def test_total_cannot_be_claimed_without_corresponding_operations(self):
        self.catalog['tasks'][0]['declared_conditional_operations'] += 10
        with self.assertRaisesRegex(ValueError, 'count is incorrect'):
            audit(self.catalog)

    def test_wait_calls_cannot_be_used_to_extend_useful_work(self):
        phase = self.catalog['tasks'][0]['phases'][0]
        phase['primitives'].append('wait')
        phase['conditional_action_ledger'].append(dict(primitive='wait', count=100))
        with self.assertRaisesRegex(ValueError, 'cannot pad'):
            audit(self.catalog)

    def test_reference_length_cannot_be_turned_into_a_minimum(self):
        self.catalog['tasks'][0]['minimum_operations_proven'] = True
        with self.assertRaisesRegex(ValueError, 'not a lower bound'):
            audit(self.catalog)

    def test_duplicate_rows_do_not_silently_double_operations(self):
        phase = self.catalog['tasks'][0]['phases'][0]
        phase['conditional_action_ledger'].append(copy.deepcopy(phase['conditional_action_ledger'][0]))
        with self.assertRaisesRegex(ValueError, 'Duplicate primitive row'):
            audit(self.catalog)

    def test_unsupported_manufacturing_verb_cannot_be_counted(self):
        phase = self.catalog['tasks'][0]['phases'][0]
        phase['primitives'].append('tighten_screw')
        phase['conditional_action_ledger'].append(dict(primitive='tighten_screw', count=1))
        with self.assertRaisesRegex(ValueError, 'Unknown primitive'):
            audit(self.catalog)


if __name__ == '__main__':
    unittest.main()
