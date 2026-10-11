import unittest

from manipulation_agent.executors.placement import surface_seating_delta


class SurfaceSeating(unittest.TestCase):
    def support(self, gap, **overrides):
        checks = {'support_ray_hits_selected_object': True,
                  'upward_support': True,
                  'selected_shelf_height': True,
                  'selected_surface_neighborhood': True}
        checks.update(overrides)
        return {'bottom_gap_m': gap, 'checks': checks}

    def test_sampler_clearance_is_lowered_to_near_contact(self):
        self.assertAlmostEqual(surface_seating_delta(self.support(.07227)), .06927)

    def test_wrong_surface_or_large_gap_is_never_seated(self):
        self.assertEqual(surface_seating_delta(self.support(.072, support_ray_hits_selected_object=False)), 0)
        self.assertEqual(surface_seating_delta(self.support(.072, selected_shelf_height=False)), 0)
        self.assertEqual(surface_seating_delta(self.support(.30)), 0)
        self.assertEqual(surface_seating_delta(self.support(None)), 0)

    def test_already_near_support_stays_untouched(self):
        self.assertEqual(surface_seating_delta(self.support(.012)), 0)


if __name__ == '__main__':
    unittest.main()
