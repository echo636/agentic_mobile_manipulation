import unittest

from manipulation_agent.executors.gt_navigation import (
    GridMap, NavigationError, mask_dynamic_obstacles, plan_navigation)


class DynamicNavigationObstacles(unittest.TestCase):
    def test_path_detours_around_current_furniture_footprint(self):
        base = GridMap(100, 100, .1, (-5., -5.), bytes([1]) * 10000)
        grid, count = mask_dynamic_obstacles(
            base, [(-.6, -.3, .6, .3)], margin=.5, start=(-2., 0.))
        self.assertGreater(count, 0)
        self.assertTrue(base.navigable(base.cell((0., 0.))))
        self.assertFalse(grid.navigable(grid.cell((0., 0.))))
        plan = plan_navigation(grid, (-2., 0.), (2., 0.), standoff=.3)
        self.assertTrue(all(grid.segment_free(a, b)
                            for a, b in zip(plan.points, plan.points[1:])))
        self.assertGreater(plan.geodesic_m, 4.)

    def test_overlap_at_start_fails_before_any_motor_step(self):
        base = GridMap(40, 40, .1, (-2., -2.), bytes([1]) * 1600)
        with self.assertRaises(NavigationError) as error:
            mask_dynamic_obstacles(base, [(-.3, -.3, .3, .3)],
                                   margin=.5, start=(0., 0.))
        self.assertEqual(error.exception.code, 'navigation_invalid_start')


if __name__ == '__main__':
    unittest.main()
