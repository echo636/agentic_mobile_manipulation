"""A holonomic base can retain its heading through a narrow elbow."""
import math
import json
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import numpy as np

from manipulation_agent.executors.navigation.forward_navigation import route_from_poses, select_forward_route
from manipulation_agent.executors.navigation.gt_navigation import GridMap, NavigationPlan, GreedyGridFollower, NavigationError
from manipulation_agent.executors.navigation.heading_collision import HeadingCollisionChecker
from manipulation_agent.executors.navigation.point_route_projection import project_forward_point


class HolonomicTests(unittest.TestCase):
    def test_loaded_base_can_leave_at_recorded_continuous_heading(self):
        # R639 action107: all five endpoints passed live visibility, but the
        # loaded robot could not join the sixteen-heading graph. This crop
        # retains the recorded body/payload layers and a 1.3m obstacle halo.
        path = Path(__file__).parent / 'fixtures/local_stances/preschool_loaded_heading_r639.npz'
        with np.load(path, allow_pickle=False) as data:
            raw = data['free']; start = tuple(data['start']); target = tuple(data['target'])
            grid = GridMap(raw.shape[1], raw.shape[0], float(data['resolution']),
                           tuple(data['origin']), raw.tobytes())
            layers = list(zip(data['raw_layers'], [row['footprint'] for row in
                              json.loads(str(data['layer_footprints_json']))]))
            collision = HeadingCollisionChecker(grid, layers, float(data['initial_layer_yaw']), data['support'])
            plans = [NavigationPlan((start[:2], tuple(pose[:2])), tuple(pose[:2]), target[:2], pose[2],
                        math.dist(start[:2], pose[:2]), math.dist(target[:2], pose[:2]), 5, 1, 0., 0)
                     for pose in data['goals']]
        found = project_forward_point(plans, start, collision, lambda: None,
                                      exact_endpoints=True, allow_lateral=True)
        self.assertIsNotNone(found)
        chosen, poses, _ = found
        self.assertEqual(poses[0], start)
        self.assertEqual(poses[1][2], start[2])
        self.assertLessEqual(math.dist(chosen.goal, target[:2]), 1.4)
        self.assertTrue(all(collision.motion_free(a, b) for a, b in zip(poses, poses[1:])))

    def test_floor_projection_reuses_clear_guide_without_relaxing_object_approach(self):
        rr, cc = np.mgrid[:61, :61]
        x, y = cc*.05-1.5, rr*.05-1.5
        free = (~((abs(x) <= .1) & (abs(y) <= .5))).astype('uint8')
        grid = GridMap(61, 61, .05, (-1.5, -1.5), free.tobytes())
        footprint = [(-.03, -.03), (.03, -.03), (.03, .03), (-.03, .03)]
        collision = HeadingCollisionChecker(grid, [(free, footprint)], 0., free)
        start, goal = (-1., 0., 0.), (1., 0.)
        blocked = NavigationPlan((start[:2], goal), goal, goal, 0., 2., 0., 1, 1, 0., 0)
        clear = replace(blocked, points=(start[:2], (-.3, -.8), (.3, -.8), (1., -.3)),
                        goal=(1., -.3), score=.3, geodesic_m=2.5)
        with patch('manipulation_agent.executors.navigation.forward_navigation.plan_forward_route',
                   side_effect=NavigationError('Forward search exhausted', 'navigation_search_exhausted')):
            chosen, route, details = select_forward_route(
                [blocked, clear], start, collision, project_point=True, allow_lateral=True)
            self.assertEqual(chosen.goal, clear.goal)
            self.assertEqual(details['search'], 'checked_holonomic_guide')
            actual = start
            for phase in route.phases:
                following = (*phase.goal, phase.final_yaw)
                self.assertTrue(collision.motion_free(actual, following))
                actual = following
            # An object approach retains its admitted endpoint tolerance;
            # the same farther guide cannot replace its required stance.
            chosen, _, _ = select_forward_route(
                [blocked, clear], start, collision, project_point=False, allow_lateral=True)
            self.assertEqual(chosen.goal, blocked.goal)

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
        with patch('manipulation_agent.executors.navigation.forward_navigation.plan_forward_route',
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
        with patch('manipulation_agent.executors.navigation.forward_navigation.simplify_forward_poses',
                   return_value=(poses, 0)):
            route = route_from_poses(plan, poses, None, allow_lateral=True)
        self.assertEqual(len(route.phases), 2)
        self.assertEqual(route.phases[0].goal, (1., 0.))
        self.assertEqual(route.phases[1].points[0], (1., 0.))


if __name__ == '__main__':
    unittest.main()
