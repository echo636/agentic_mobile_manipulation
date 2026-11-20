"""A navigation goal must not claim visibility through the robot's own arm."""
import itertools
import unittest

from manipulation_agent.executors.self_depth_filter import (
    prepare_self_hulls, segment_hits_self_hull,
)


def box(x0, x1, y0, y1, z0, z1):
    return list(itertools.product((x0, x1), (y0, y1), (z0, z1)))


class SelfOcclusionNavigationTests(unittest.TestCase):
    def test_arm_between_candidate_camera_and_selected_object_is_rejected(self):
        # The previous navigation check ignored the robot in its raytest and
        # accepted this candidate even though the RGB arm covers the target.
        hulls, _ = prepare_self_hulls({'right_arm': box(.35, .65, -.12, .12, .9, 1.5)})
        self.assertTrue(segment_hits_self_hull((0., 0., 1.8), (1., 0., .3), hulls))
        self.assertFalse(segment_hits_self_hull((0., .5, 1.8), (1., .5, .3), hulls))

    def test_robot_geometry_behind_target_is_not_an_occluder(self):
        hulls, _ = prepare_self_hulls({'carried': box(1.1, 1.3, -.1, .1, .2, .4)})
        self.assertFalse(segment_hits_self_hull((0., 0., 1.8), (1., 0., .3), hulls))


if __name__ == '__main__':
    unittest.main()
