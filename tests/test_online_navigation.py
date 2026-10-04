"""Sensor-map safety and online replanning, independent of the simulator."""
import math
from types import SimpleNamespace
import unittest

import numpy as np

from manipulation_agent.executors.gt_navigation import NavigationError
from manipulation_agent.executors.online_navigation import observed_grid, plan_online_navigation


def snapshot(array, resolution=.1, origin=(0., 0.), sequence=1):
    values = np.asarray(array, dtype=np.uint8)
    return SimpleNamespace(width=values.shape[1], height=values.shape[0],
                           resolution=resolution, origin=origin,
                           occupancy=values.tobytes(), sequence=sequence)


class ObservedGridTests(unittest.TestCase):
    def test_unknown_and_occupied_are_inflated_without_mutating_input(self):
        data = np.zeros((15, 15), dtype=np.uint8)
        data[7, 7] = 100
        data[3, 3] = 255
        source = snapshot(data)
        grid = observed_grid(source, robot_radius=.16)
        self.assertFalse(grid.navigable((7, 7)))
        self.assertFalse(grid.navigable((7, 9)))  # cell edge is only .15m away
        self.assertTrue(grid.navigable((7, 10)))
        self.assertFalse(grid.navigable((3, 4)))
        self.assertFalse(grid.navigable((0, 10)))  # unknown beyond observed map
        self.assertEqual(source.occupancy, data.tobytes())

    def test_no_start_clearing_or_unobserved_recovery_teleport(self):
        data = np.zeros((30, 30), dtype=np.uint8)
        for value in (100, 255):
            data[10, 10] = value
            grid = observed_grid(snapshot(data), robot_radius=0)
            with self.assertRaises(NavigationError) as raised:
                plan_online_navigation(grid, (1., 1.), (2., 1.))
            self.assertEqual(raised.exception.code, 'navigation_invalid_start')
            self.assertFalse(grid.navigable((10, 10)))

    def test_footprint_rejects_narrow_corridor(self):
        data = np.full((25, 40), 100, dtype=np.uint8)
        data[10:15, :] = 0  # 0.5m corridor cannot fit a diameter0.6m robot
        grid = observed_grid(snapshot(data), robot_radius=.3)
        self.assertFalse(grid.navigable((12, 10)))


class OnlinePlannerTests(unittest.TestCase):
    def test_routes_around_obstacle_without_corner_cutting(self):
        data = np.zeros((40, 55), dtype=np.uint8)
        data[10:31, 25] = 100
        grid = observed_grid(snapshot(data), robot_radius=.1)
        plan = plan_online_navigation(grid, (1., 2.), (4.5, 2.), fixed_goal=(3.8, 2.))
        self.assertGreater(plan.geodesic_m, 3.)
        self.assertGreater(len(plan.points), 2)
        self.assertTrue(all(grid.segment_free(a, b) for a, b in zip(plan.points, plan.points[1:])))
        self.assertEqual(plan.goal, (3.8, 2.))

    def test_unobserved_strip_blocks_route_until_new_sensor_snapshot(self):
        data = np.zeros((30, 65), dtype=np.uint8)
        data[:, 28:35] = 255
        before = observed_grid(snapshot(data), robot_radius=.1)
        with self.assertRaises(NavigationError) as raised:
            plan_online_navigation(before, (1., 1.5), (5., 1.5))
        self.assertEqual(raised.exception.code, 'navigation_unobserved_route')
        data[10:21, 28:35] = 0  # Only these cells became observed by new rays.
        after = observed_grid(snapshot(data, sequence=2), robot_radius=.1)
        plan = plan_online_navigation(after, (1., 1.5), (5., 1.5))
        self.assertLessEqual(math.dist(plan.goal, (5., 1.5)), 1.4)
        self.assertFalse(before.navigable((15, 30)))
        self.assertTrue(after.navigable((15, 30)))

    def test_new_obstacle_forces_replan_to_same_endpoint(self):
        data = np.zeros((40, 55), dtype=np.uint8)
        first = observed_grid(snapshot(data), robot_radius=.1)
        plan = plan_online_navigation(first, (1., 2.), (4.5, 2.), fixed_goal=(3.8, 2.))
        data[10:31, 25] = 100
        second = observed_grid(snapshot(data, sequence=2), robot_radius=.1)
        self.assertFalse(second.segment_free(plan.points[0], plan.points[-1]))
        revised = plan_online_navigation(second, (1., 2.), (4.5, 2.), fixed_goal=plan.goal)
        self.assertEqual(revised.goal, plan.goal)
        self.assertGreater(revised.geodesic_m, plan.geodesic_m)
        data[20, 38] = 100
        third = observed_grid(snapshot(data, sequence=3), robot_radius=.1)
        with self.assertRaises(NavigationError) as raised:
            plan_online_navigation(third, (1., 2.), (4.5, 2.), fixed_goal=plan.goal)
        self.assertEqual(raised.exception.code, 'navigation_path_blocked')

    def test_fixed_endpoint_survives_float32_crop_origin_roundoff(self):
        # Real radio r1 map13 -> map14 retained the same free world cell,
        # but a float32 crop origin shifted its reported centre by 0.2um.
        values = np.zeros((240, 310), dtype=np.uint8)
        radius = .40188753604888916
        goal = (2.47685170173645, 3.2073160171508794)
        target = (2.903395652770996, 3.911875009536743)
        origin = (-3.4731483459472656, -4.342683792114258)
        grid = observed_grid(snapshot(values, .05, origin), robot_radius=radius)
        plan = plan_online_navigation(grid, (4.12717342376709, 3.6326491832733154),
                                      target, fixed_goal=goal)
        self.assertGreater(math.dist(grid.world(grid.cell(goal)), goal), 1e-7)
        self.assertEqual(plan.goal, goal)
        self.assertEqual(plan.points[-1], goal)
        # Recentring the grid must not move a safe world destination either.
        shifted = observed_grid(snapshot(values, .05, (origin[0]+.001, origin[1])),
                                robot_radius=radius)
        shifted_plan = plan_online_navigation(shifted, (4.127, 3.633), target, fixed_goal=goal)
        self.assertEqual(shifted_plan.goal, goal)
        self.assertEqual(shifted_plan.points[-1], goal)
        for blocked_value in (100, 255):
            values[grid.cell(goal)] = blocked_value
            blocked = observed_grid(snapshot(values, .05, origin), robot_radius=radius)
            with self.assertRaises(NavigationError):
                plan_online_navigation(blocked, (4.127, 3.633), target, fixed_goal=goal)

    def test_clicked_object_can_be_occupied_but_approach_is_free(self):
        data = np.zeros((35, 50), dtype=np.uint8)
        data[13:18, 33:38] = 100
        grid = observed_grid(snapshot(data), robot_radius=.15)
        plan = plan_online_navigation(grid, (1., 1.5), (3.5, 1.5))
        self.assertFalse(grid.navigable(grid.cell(plan.target)))
        self.assertTrue(grid.navigable(grid.cell(plan.goal)))
        self.assertAlmostEqual(math.dist(plan.goal, plan.target), .7, delta=.13)
        self.assertLessEqual(math.dist(plan.goal, plan.target), 1.4)

    def test_disconnected_nearest_candidate_does_not_cross_unknown(self):
        data = np.full((30, 50), 255, dtype=np.uint8)
        data[3:27, 3:22] = 0
        data[3:27, 26:47] = 0
        grid = observed_grid(snapshot(data), robot_radius=.05)
        # Target is near both islands. Valid selection remains on start island.
        plan = plan_online_navigation(grid, (1., 1.5), (2.7, 1.5), max_target_distance=1.0)
        self.assertLess(plan.goal[0], 2.2)
        self.assertTrue(all(grid.segment_free(a,b) for a,b in zip(plan.points,plan.points[1:])))

    def test_periodic_cancellation_is_propagated(self):
        grid = observed_grid(snapshot(np.zeros((40, 55), dtype=np.uint8)), robot_radius=0)
        def stop():
            raise TimeoutError('episode expired')
        with self.assertRaisesRegex(TimeoutError, 'episode expired'):
            plan_online_navigation(grid, (1., 1.), (4., 2.), check_cancel=stop)


if __name__ == '__main__':
    unittest.main()
