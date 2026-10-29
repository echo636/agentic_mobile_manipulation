import unittest

import numpy as np

from manipulation_agent.executors.doorway_traversability import merge_open_door_cells
from manipulation_agent.executors.gt_navigation import GridMap, NavigationError, plan_navigation


class DoorwayTraversabilityTests(unittest.TestCase):
    def test_only_the_opened_local_door_connects_its_rooms(self):
        closed = np.full((60, 60), 255, dtype=np.uint8)
        closed[30, :] = 0
        opened = closed.copy()
        opened[30, 13:18] = 255
        opened[30, 43:48] = 255
        start, target = (-1.5, -1.), (-1.5, 1.)
        merged, count = merge_open_door_cells(
            closed, opened, center_xy=(-1.5, 0.), resolution=.1, radius_m=.5)
        self.assertEqual(count, 5)
        self.assertTrue(np.all(merged[30, 13:18] == 255))
        self.assertTrue(np.all(merged[30, 43:48] == 0))
        grid = GridMap(60, 60, .1, (-3., -3.), (merged != 0).astype('uint8').tobytes())
        plan = plan_navigation(grid, start, target, standoff=.2, max_target_distance=.5)
        self.assertGreater(plan.goal[1], .5)
        closed_grid = GridMap(60, 60, .1, (-3., -3.), (closed != 0).astype('uint8').tobytes())
        with self.assertRaises(NavigationError):
            plan_navigation(closed_grid, start, target, standoff=.2, max_target_distance=.5)


if __name__ == '__main__':
    unittest.main()
