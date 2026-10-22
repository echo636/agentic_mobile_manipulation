"""A holonomic base can retain its heading through a narrow elbow."""
import math
import unittest
from unittest.mock import patch

import numpy as np

from manipulation_agent.executors.forward_navigation import route_from_poses, select_forward_route
from manipulation_agent.executors.gt_navigation import GridMap, NavigationPlan, GreedyGridFollower, NavigationError
from manipulation_agent.executors.heading_collision import HeadingCollisionChecker
from manipulation_agent.executors.point_route_projection import project_forward_point


class HolonomicTests(unittest.TestCase):
    def test_sideways_roadmap_and_follower_keep_full_body_clear(self):
        rr, cc = np.mgrid[:81, :81]
        x, y = cc*.025-1.5, rr*.025-.4
        free = (((x >= -1.4) & (x <= .34) & (abs(y) <= .15)) |
                ((abs(x) <= .34) & (y >= -.15) & (y <= 1.5))).astype('uint8')
        grid = GridMap(81, 81, .025, (-1.5, -.4), free.tobytes())
        footprint = [(-.25, -.045), (.25, -.045), (.25, .045), (-.25, .045)]
        collision = HeadingCollisionChecker(grid, [(free, footprint)], 0., free)
        start, goal = (-1., 0., 0.), (0., 1.)
        plan = NavigationPlan((start[:2], goal), goal, goal, 0., 2., 0., 1, 1, 0., 0)
        found = project_forward_point([plan], start, collision, lambda: None,
                                      exact_endpoints=True, allow_lateral=True)
        self.assertIsNotNone(found)
        chosen, poses, details = found
        self.assertTrue(details['holonomic_translations'])
        route = route_from_poses(chosen, poses, collision, allow_lateral=True)
        # The public navigation selector must reach the same capability after
        # its preferred forward/reverse planner exhausts its budget.
        with patch('manipulation_agent.executors.forward_navigation.plan_forward_route',
                   side_effect=NavigationError('Archived forward search failure', 'navigation_search_exhausted')):
            chosen, route, selection = select_forward_route([plan], start, collision, allow_lateral=True)
            self.assertEqual(chosen.goal, plan.goal)
            self.assertEqual(selection['forward_failure'], 'navigation_search_exhausted')
            with self.assertRaises(NavigationError):
                select_forward_route([plan], start, collision, allow_lateral=False)
        actual = start
        for phase in route.phases:
            follower = GreedyGridFollower(collision.grid, phase, .02,
                forward_only=phase.motion_mode != 'holonomic', motion_check=collision.motion_free)
            for _ in range(1000):
                command = follower.next_pose(actual)
                if command is None:
                    break
                self.assertTrue(collision.motion_free(actual, command))
                actual = command
            else:
                self.fail('Follower did not finish')
        self.assertLess(math.dist(actual[:2], goal), .003)

    def test_same_heading_sideways_corner_is_not_merged_through_obstacle(self):
        poses = [(0., 0., 0.), (1., 0., 0.), (1., 1., 0.)]
        plan = NavigationPlan(tuple(p[:2] for p in poses), (1., 1.), (1., 1.), 0., 2., 0., 1, 1, 0., 0)
        with patch('manipulation_agent.executors.forward_navigation.simplify_forward_poses',
                   return_value=(poses, 0)):
            route = route_from_poses(plan, poses, None, allow_lateral=True)
        self.assertEqual(len(route.phases), 2)
        self.assertEqual(route.phases[0].goal, (1., 0.))
        self.assertEqual(route.phases[1].points[0], (1., 0.))


if __name__ == '__main__':
    unittest.main()
