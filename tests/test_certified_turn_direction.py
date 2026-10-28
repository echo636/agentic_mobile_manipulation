"""A checked half-turn must survive simulator yaw rounding and wraparound."""
from dataclasses import replace
import math
import unittest

from manipulation_agent.executors.navigation.forward_navigation import angle, route_from_poses
from manipulation_agent.executors.navigation.gt_navigation import GridMap, GreedyGridFollower, NavigationError, NavigationPlan


class CertifiedTurnDirectionTests(unittest.TestCase):
    def test_half_turn_keeps_the_clear_side_despite_feedback_residual(self):
        grid = GridMap(21, 21, .1, (-1., -1.), bytes([1])*441)
        # Recorded lunch-box approach: reverse travel followed by a pi turn.
        for start, direction in ((-.7006502186569217, 1), (.7006502186569217, -1),
                                 (2.8, 1), (-2.8, -1)):
            with self.subTest(start=start, direction=direction):
                end = start+direction*math.pi
                def clear(a, b):
                    delta = angle(b[2]-a[2])
                    return all(math.sin(direction*(a[2]+delta*i/20-start)) >= -1e-4
                               for i in range(21))
                class Collision:
                    motion_free = staticmethod(clear)
                plan = NavigationPlan(((0., 0.),), (0., 0.), (0., 0.), end, 0., 0., 1, 1, 0., 0)
                route = route_from_poses(plan, [(0., 0., start), (0., 0., end)], Collision())
                phase = route.phases[0]
                measured = (0., 0., angle(start-direction*1e-6))
                # The old shortest-angle rule takes the obstructed half-circle.
                old = GreedyGridFollower(grid, replace(phase, turn_start_yaw=None), 1/30,
                                         motion_check=clear)
                with self.assertRaises(NavigationError):
                    old.next_pose(measured)
                follower = GreedyGridFollower(grid, phase, 1/30, motion_check=clear)
                for step in range(100):
                    command = follower.next_pose(measured)
                    if command is None:
                        break
                    self.assertLessEqual(abs(angle(command[2]-measured[2])), math.pi/90+1e-10)
                    measured = (command[0], command[1], angle(command[2]-direction*2e-7))
                else:
                    self.fail('Certified half-turn did not converge')
                self.assertLess(abs(angle(measured[2]-end)), follower.final_yaw_tolerance)

    def test_certified_direction_still_checks_each_command_for_collision(self):
        grid = GridMap(21, 21, .1, (-1., -1.), bytes([1])*441)
        plan = NavigationPlan(((0., 0.),), (0., 0.), (0., 0.), math.pi,
                              0., 0., 1, 1, 0., 0, turn_start_yaw=0.)
        follower = GreedyGridFollower(grid, plan, 1/30, motion_check=lambda a, b: False)
        with self.assertRaisesRegex(NavigationError, 'swept collision'):
            follower.next_pose((0., 0., -1e-6))
