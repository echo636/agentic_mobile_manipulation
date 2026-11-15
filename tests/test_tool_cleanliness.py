import unittest

from manipulation_agent.executors.tool_cleanliness import (
    ToolCleanlinessTracker,
    validate_tool_cleanliness,
)


def _sample_cleanliness_spec():
    return {
        'schema_version': 2,
        'id': 'cleanliness_test',
        'roles': {
            'sink': 'sink_0',
            'rag': 'rag_0',
            'dining_table': 'table_0',
            'prep_counter': 'prep_0',
            'bar_counter': 'bar_0',
            'cabinet_shelf': 'shelf_0',
            'stove_top': 'stove_0',
            'waste_surround': 'waste_0',
        },
        'tool_cleanliness': {
            'tool': 'rag',
            'water_sources': ['sink'],
            'surface_levels': {
                'dining_table': 1,
                'prep_counter': 1,
                'bar_counter': 2,
                'cabinet_shelf': 2,
                'stove_top': 3,
                'waste_surround': 3,
            },
        },
    }


class TestToolCleanliness(unittest.TestCase):

    def test_validate_spec_success(self):
        spec = _sample_cleanliness_spec()
        validate_tool_cleanliness(spec)

    def test_validate_spec_missing_tool(self):
        spec = _sample_cleanliness_spec()
        del spec['tool_cleanliness']['tool']
        with self.assertRaises(ValueError):
            validate_tool_cleanliness(spec)

    def test_validate_spec_unknown_tool_role(self):
        spec = _sample_cleanliness_spec()
        spec['tool_cleanliness']['tool'] = 'unknown_tool'
        with self.assertRaises(ValueError):
            validate_tool_cleanliness(spec)

    def test_validate_spec_unknown_water_source(self):
        spec = _sample_cleanliness_spec()
        spec['tool_cleanliness']['water_sources'] = ['unknown_sink']
        with self.assertRaises(ValueError):
            validate_tool_cleanliness(spec)

    def test_validate_spec_invalid_surface_level(self):
        spec = _sample_cleanliness_spec()
        spec['tool_cleanliness']['surface_levels']['dining_table'] = 4
        with self.assertRaises(ValueError):
            validate_tool_cleanliness(spec)

        spec['tool_cleanliness']['surface_levels']['dining_table'] = 0
        with self.assertRaises(ValueError):
            validate_tool_cleanliness(spec)

    def test_tracker_initial_state(self):
        spec = _sample_cleanliness_spec()
        tracker = ToolCleanlinessTracker.from_spec(spec)
        self.assertIsNotNone(tracker)
        self.assertEqual(tracker.dirtiness, 0)
        self.assertFalse(tracker.has_violation())
        self.assertEqual(tracker.wipe_count, 0)
        self.assertEqual(tracker.soak_count, 0)

    def test_tracker_monotonic_wipe_order(self):
        spec = _sample_cleanliness_spec()
        tracker = ToolCleanlinessTracker.from_spec(spec)

        # Level 1: dining_table
        ok = tracker.record_wipe(1, surface='dining_table')
        self.assertTrue(ok)
        self.assertEqual(tracker.dirtiness, 1)
        self.assertFalse(tracker.has_violation())

        # Level 1 again: prep_counter
        ok = tracker.record_wipe(1, surface='prep_counter')
        self.assertTrue(ok)
        self.assertEqual(tracker.dirtiness, 1)
        self.assertFalse(tracker.has_violation())

        # Level 2: bar_counter
        ok = tracker.record_wipe(2, surface='bar_counter')
        self.assertTrue(ok)
        self.assertEqual(tracker.dirtiness, 2)
        self.assertFalse(tracker.has_violation())

        # Level 3: stove_top
        ok = tracker.record_wipe(3, surface='stove_top')
        self.assertTrue(ok)
        self.assertEqual(tracker.dirtiness, 3)
        self.assertFalse(tracker.has_violation())

        # Level 3 again: waste_surround
        ok = tracker.record_wipe(3, surface='waste_surround')
        self.assertTrue(ok)
        self.assertEqual(tracker.dirtiness, 3)
        self.assertFalse(tracker.has_violation())

    def test_tracker_cross_contamination_violation(self):
        spec = _sample_cleanliness_spec()
        tracker = ToolCleanlinessTracker.from_spec(spec)

        # Wipe Level 3 (dirty stove)
        tracker.record_wipe(3, surface='stove_top')
        self.assertEqual(tracker.dirtiness, 3)
        self.assertFalse(tracker.has_violation())

        # Wipe Level 1 without soaking: violation!
        ok = tracker.record_wipe(1, surface='dining_table')
        self.assertFalse(ok)
        self.assertTrue(tracker.has_violation())
        # Dirtiness remains max(3, 1) = 3
        self.assertEqual(tracker.dirtiness, 3)

    def test_tracker_decontamination_reset(self):
        spec = _sample_cleanliness_spec()
        tracker = ToolCleanlinessTracker.from_spec(spec)

        # Wipe Level 3 (dirty stove)
        tracker.record_wipe(3, surface='stove_top')
        self.assertEqual(tracker.dirtiness, 3)

        # Soak in sink -> reset D to 0
        reset = tracker.record_soak('sink')
        self.assertTrue(reset)
        self.assertEqual(tracker.dirtiness, 0)
        self.assertFalse(tracker.has_violation())

        # Now wiping Level 1 is completely legal!
        ok = tracker.record_wipe(1, surface='dining_table')
        self.assertTrue(ok)
        self.assertEqual(tracker.dirtiness, 1)
        self.assertFalse(tracker.has_violation())

    def test_tracker_soak_non_water_source(self):
        spec = _sample_cleanliness_spec()
        tracker = ToolCleanlinessTracker.from_spec(spec)

        tracker.record_wipe(2, surface='bar_counter')
        self.assertEqual(tracker.dirtiness, 2)

        # Soaking in non-water-source should not reset
        reset = tracker.record_soak('dining_table')
        self.assertFalse(reset)
        self.assertEqual(tracker.dirtiness, 2)

    def test_tracker_to_dict(self):
        spec = _sample_cleanliness_spec()
        tracker = ToolCleanlinessTracker.from_spec(spec)
        tracker.record_wipe(1, surface='dining_table')
        tracker.record_soak('sink')

        d = tracker.to_dict()
        self.assertIn('dirtiness', d)
        self.assertIn('violation', d)
        self.assertIn('wipe_count', d)
        self.assertIn('soak_count', d)
        self.assertIn('history', d)
        self.assertEqual(d['wipe_count'], 1)
        self.assertEqual(d['soak_count'], 1)

    def test_public_receipt_factual_and_non_identifying(self):
        spec = _sample_cleanliness_spec()
        tracker = ToolCleanlinessTracker.from_spec(spec)
        r0 = tracker.public_receipt('grasp')
        self.assertEqual(r0['tool_dirtiness_level'], 0)
        self.assertEqual(r0['tool_state'], 'clean')
        self.assertFalse(r0['cross_contamination_occurred'])

        ok = tracker.record_wipe(3, surface='stove_top')
        r1 = tracker.public_receipt('wipe', compliant=ok)
        self.assertEqual(r1['tool_dirtiness_level'], 3)
        self.assertEqual(r1['tool_state'], 'tier_3_heavy_stain')
        self.assertFalse(r1['cross_contamination_occurred'])
        self.assertNotIn('stove_top', r1['message'])

        ok2 = tracker.record_wipe(1, surface='dining_table')
        r2 = tracker.public_receipt('wipe', compliant=ok2)
        self.assertFalse(ok2)
        self.assertTrue(r2['cross_contamination_occurred'])
        self.assertIn('Cross-contamination occurred', r2['message'])

        was_water = tracker.record_soak('sink')
        r3 = tracker.public_receipt('soak_reset' if was_water else 'soak')
        self.assertEqual(r3['tool_dirtiness_level'], 0)
        self.assertEqual(r3['tool_state'], 'clean')


if __name__ == '__main__':
    unittest.main()
