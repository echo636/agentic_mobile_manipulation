"""A navigation goal must not claim visibility through the robot's own arm."""
import itertools
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from manipulation_agent.executors.navigation.self_occlusion import (
    prepare_self_hulls, segment_hits_self_hull,
)


def box(x0, x1, y0, y1, z0, z1):
    return list(itertools.product((x0, x1), (y0, y1), (z0, z1)))


class SelfOcclusionNavigationTests(unittest.TestCase):
    def test_carried_cloth_uses_current_particles_for_self_occlusion(self):
        import torch
        from manipulation_agent.executors.omnigibson_rgb import RGBBackend

        backend = SimpleNamespace()
        base = torch.tensor([2., -3., .1])
        cloth_points = torch.tensor(box(.35, .65, -.12, .12, .9, 1.5)) + base
        cloth = SimpleNamespace(prim_path='/napkin', compute_particle_positions=lambda: cloth_points)
        arm = SimpleNamespace(prim_path='/arm', visual_boundary_points_world=
                              torch.tensor(box(.35, .65, .8, 1., .9, 1.5)) + base)
        backend.robot = SimpleNamespace(links={'arm': arm},
            get_position_orientation=lambda: (base, torch.tensor([0., 0., 0., 1.])))
        backend._get_held = lambda: SimpleNamespace(links={'cloth': cloth})
        backend._carry_contents = []
        transform = ModuleType('omnigibson.utils.transform_utils')
        transform.quat2mat = lambda _: torch.eye(3)
        modules = {'omnigibson': ModuleType('omnigibson'),
                   'omnigibson.utils': ModuleType('omnigibson.utils'),
                   'omnigibson.utils.transform_utils': transform}
        with patch.dict('sys.modules', modules):
            hulls, report = RGBBackend._navigation_self_hulls(backend)
            self.assertEqual({hull.link for hull in hulls}, {'/arm', '/napkin'})
            self.assertFalse(report['skipped_links'])
            self.assertTrue(segment_hits_self_hull((0., 0., 1.8), (1., 0., .3), hulls))
            # A subsequent deformation must be read afresh, not a rest-mesh hull.
            cloth_points[:, 1] += 2.
            hulls, _ = RGBBackend._navigation_self_hulls(backend)
            self.assertFalse(segment_hits_self_hull((0., 0., 1.8), (1., 0., .3), hulls))
            self.assertTrue(segment_hits_self_hull((0., .9, 1.8), (1., .9, .3), hulls))

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
