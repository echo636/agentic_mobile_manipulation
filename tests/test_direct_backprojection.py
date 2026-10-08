"""Direct selected-pixel depth geometry and independent mesh identity routing."""
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from manipulation_agent.contracts import SkillError
from manipulation_agent.executors.omnigibson_rgb import RGBBackend


@unittest.skipUnless(importlib.util.find_spec('torch'), 'Requires torch for real tensor geometry')
class DirectBackprojection(unittest.TestCase):
    def setUp(self):
        import torch
        self.torch = torch
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.backend = b = RGBBackend.__new__(RGBBackend)
        b.output = Path(folder.name)
        b.torch = torch
        self.rotation = torch.tensor([[0., 0., 1.], [0., 1., 0.], [-1., 0., 0.]])
        transforms = ModuleType('omnigibson.utils.transform_utils')
        transforms.quat2mat = Mock(return_value=self.rotation)
        sampling = ModuleType('omnigibson.utils.sampling_utils')
        self.physics_ray = sampling.raytest = Mock(side_effect=AssertionError('physics ray queried'))
        utils = ModuleType('omnigibson.utils')
        utils.transform_utils, utils.sampling_utils = transforms, sampling
        states = ModuleType('omnigibson.object_states')
        states.Open, states.Inside = type('Open', (), {}), type('Inside', (), {})
        modules = {'omnigibson': ModuleType('omnigibson'), 'omnigibson.utils': utils,
                   'omnigibson.utils.transform_utils': transforms,
                   'omnigibson.utils.sampling_utils': sampling,
                   'omnigibson.object_states': states}
        override = patch.dict(sys.modules, modules)
        override.start()
        self.addCleanup(override.stop)
        depths = torch.full((5, 7), .4)
        depths[1, 4] = 2.4
        self.frame = {'width': 7, 'height': 5, 'depth_linear': depths,
                      'position': torch.tensor([10., 20., 30.]),
                      'orientation': torch.tensor([0., 2**-.5, 0., 2**-.5]),
                      'intrinsic': torch.tensor([[2., 0., 3.], [0., 4., 2.], [0., 0., 1.]])}
        b.current_frames = {'current-front': self.frame}
        b.robot = SimpleNamespace(links={})
        self.target = {'image_ref': 'current-front', 'point': [.6, .25]}
        self.expected = torch.tensor([7.6, 20.6, 28.8])
        b.robot.get_position_orientation = lambda: (self.expected.clone(), torch.tensor([0., 0., 0., 1.]))
        self.object = SimpleNamespace(prim_path='/selected', states={})
        # Identity's triangle is deliberately 20cm from the depth point.
        self.visual = {'visual_mesh': '/selected/mesh',
                       'visual_triangle_position': (self.expected+torch.tensor([.2, 0., 0.])).tolist()}

    def test_integer_pixel_axial_depth_and_camera_transform_define_position(self):
        with patch('manipulation_agent.executors.visual_mesh_grounding.query_visual_surface',
                   return_value=(self.object, self.visual)) as query:
            owner, point, diagnostic = self.backend._ground(self.target)
        self.assertIs(owner, self.object)
        self.torch.testing.assert_close(point, self.expected)
        self.assertEqual(diagnostic['raster_pixel'], [4, 1])
        self.torch.testing.assert_close(self.torch.tensor(diagnostic['hit_position']), self.expected)
        # Depth is optical-axis depth, not distance along a normalized ray.
        expected_direction = self.torch.tensor([-1., .25, -.5])
        self.assertEqual(len(query.call_args.args), 3)
        self.assertIs(query.call_args.args[0], self.backend)
        self.torch.testing.assert_close(query.call_args.args[1], self.frame['position'])
        self.torch.testing.assert_close(query.call_args.args[2], expected_direction/expected_direction.norm())
        self.assertGreater(float(self.torch.linalg.norm(point-self.torch.tensor(self.visual['visual_triangle_position']))), .015)
        self.physics_ray.assert_not_called()

    def test_depth_only_grounding_works_without_any_mesh_owner(self):
        with patch('manipulation_agent.executors.visual_mesh_grounding.query_visual_surface',
                   side_effect=AssertionError('mesh identity queried')) as query:
            owner, point, diagnostic = self.backend._ground(self.target, require_object=False)
        self.assertIsNone(owner)
        self.torch.testing.assert_close(point, self.expected)
        self.assertEqual(diagnostic['raster_pixel'], [4, 1])
        query.assert_not_called()
        self.physics_ray.assert_not_called()

    def test_navigate_to_uses_direct_point_without_mesh_or_object_dependency(self):
        b = self.backend
        b._navigate = Mock(return_value={'nav_status': 'reached'})
        with patch('manipulation_agent.executors.visual_mesh_grounding.query_visual_surface',
                   side_effect=AssertionError('navigation queried mesh')) as query:
            result = b.execute_visual('navigate_to', self.target, 123)
        self.assertEqual(result['nav_status'], 'reached')
        anchor, budget = b._navigate.call_args.args
        self.assertEqual(budget, 123)
        self.torch.testing.assert_close(anchor.get_position_orientation()[0], self.expected)
        self.assertIsNone(anchor.selected_object)
        query.assert_not_called()
        self.physics_ray.assert_not_called()

    def test_manipulation_still_requires_ray_owner_and_uses_depth_point(self):
        b = self.backend
        b.ideal_carry = True
        b._get_held = lambda: SimpleNamespace(name='held')
        b._checked_place_on_top = Mock(return_value={'placed': True})
        with patch('manipulation_agent.executors.visual_mesh_grounding.query_visual_surface',
                   return_value=(self.object, self.visual)) as query:
            result = b.execute_visual('place_on_top', self.target, 100, placement_yaw_degrees=15)
        self.assertTrue(result['placed'])
        owner, budget, point, yaw = b._checked_place_on_top.call_args.args
        self.assertIs(owner, self.object)
        self.assertEqual((budget, yaw), (100, 15))
        self.torch.testing.assert_close(point, self.expected)
        query.assert_called_once()
        self.physics_ray.assert_not_called()

    def test_invalid_depth_and_stale_capture_fail_before_identity_query(self):
        with patch('manipulation_agent.executors.visual_mesh_grounding.query_visual_surface') as query:
            for depth in [float('nan'), float('inf'), 0., -1., 30.]:
                with self.subTest(depth=depth):
                    self.frame['depth_linear'][1, 4] = depth
                    with self.assertRaises(SkillError) as error:
                        self.backend._ground(self.target, require_object=False)
                    self.assertEqual(error.exception.code, 'no_surface_at_point')
            with self.assertRaises(SkillError) as error:
                self.backend._ground({'image_ref': 'expired', 'point': [.6, .25]})
            self.assertEqual(error.exception.code, 'stale_image_ref')
        query.assert_not_called()
        self.physics_ray.assert_not_called()


if __name__ == '__main__':
    unittest.main()
