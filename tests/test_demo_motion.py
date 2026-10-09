"""The demo path must be bounded and end at the solved robot posture."""
import unittest

from manipulation_agent.executors.demo_motion import eased_positions


class DemoMotionPathTests(unittest.TestCase):
    def test_reach_uses_exactly_budgeted_steps_and_reaches_endpoint(self):
        poses = list(eased_positions(0.0, 1.0, 12))
        self.assertEqual(len(poses), 12)
        self.assertEqual(poses[-1], 1.0)
        self.assertTrue(all(a < b for a, b in zip(poses, poses[1:])))
        self.assertLess(poses[0], poses[5] - poses[4])
        self.assertLess(poses[-1] - poses[-2], poses[5] - poses[4])

    def test_reach_rejects_zero_control_steps(self):
        with self.assertRaises(ValueError):
            list(eased_positions(0.0, 1.0, 0))
