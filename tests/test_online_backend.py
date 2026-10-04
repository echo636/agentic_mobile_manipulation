"""Owner-thread integration checks without starting OmniGibson or a mapper."""
import copy
import math
from pathlib import Path
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from manipulation_agent.executors.online_backend import OnlineNavigation
from manipulation_agent.observations.rig import camera_mount


class Tensor:
    def __init__(self, value):
        self.value = np.asarray(value)

    def detach(self):
        return self

    def cpu(self):
        return self

    def numpy(self):
        return self.value

    def __getitem__(self, item):
        value = self.value[item]
        return value.item() if np.ndim(value) == 0 else Tensor(value)


def fake_og_modules():
    og = ModuleType('omnigibson')
    utils = ModuleType('omnigibson.utils')
    transforms = ModuleType('omnigibson.utils.transform_utils')
    # Fixtures carry full rotation matrices in place of quaternion tensors.
    transforms.quat2mat = lambda orientation: orientation
    utils.transform_utils = transforms
    og.utils = utils
    return {'omnigibson': og, 'omnigibson.utils': utils,
            'omnigibson.utils.transform_utils': transforms}


class OnlineBackendTests(unittest.TestCase):
    def backend(self, output):
        backend = OnlineNavigation()
        backend.output = Path(output)
        backend.steps = 12
        backend.deadline = SimpleNamespace(check=Mock())
        backend.og = SimpleNamespace(sim=SimpleNamespace(get_sim_step_dt=lambda: .01,
                                                        step=Mock(side_effect=AssertionError('physics moved'))))
        backend.robot = SimpleNamespace(get_position_orientation=lambda:
                                        (Tensor([10., 20., 0.]), Tensor(np.eye(3))))
        backend._online_floor_height = 0.
        backend._online_capture = 0
        backend._render_rgb_views = Mock()
        backend._sensor_packets = {}
        backend._sensor_intrinsics = {}
        backend.rig = {}
        for view in ('front', 'back', 'left', 'right'):
            offset, rotation = camera_mount(view, 1.5, radius=.35, pitch_degrees=0)
            position = np.array(offset) + [10, 20, 0]
            backend.rig[view] = SimpleNamespace(get_position_orientation=lambda p=position, r=rotation:
                                               (Tensor(p), Tensor(r)))
            depth = np.full((9, 9), np.nan)
            depth[8, 4] = 1.5  # In-band surface on a pixel selected by stride 4.
            backend._sensor_packets[view] = ({'depth_linear': Tensor(depth)}, {})
            backend._sensor_intrinsics[view] = Tensor([[4.5, 0, 4], [0, 4.5, 4], [0, 0, 1]])
        backend.current_frames = {'public-frame': object()}
        backend.image_files = {'public-frame': Path('public.jpg')}
        snapshot = object()
        backend._online_mapper = SimpleNamespace(update=Mock(return_value=snapshot), close=Mock())
        return backend, snapshot

    def test_prepared_capture_is_reused_without_render_physics_or_public_reference_changes(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict('sys.modules', fake_og_modules()):
            backend, snapshot = self.backend(folder)
            original_frames, original_files = backend.current_frames, backend.image_files
            result = backend._update_online_map(rendered=True)
            self.assertIs(result, snapshot)
            self.assertIs(backend._online_snapshot, snapshot)
            backend._render_rgb_views.assert_not_called()
            backend.og.sim.step.assert_not_called()
            self.assertIs(backend.current_frames, original_frames)
            self.assertIs(backend.image_files, original_files)
            packets = backend._online_mapper.update.call_args.args[0]
            self.assertEqual(len(packets), 4)
            self.assertEqual(len({tuple(p['origin']) for p in packets}), 4)
            np.testing.assert_allclose(packets[0]['origin'], [.35, 0, 0], atol=1e-12)
            np.testing.assert_allclose(packets[0]['returns'], [[1.85, 0, 0]], atol=1e-12)
            self.assertEqual(backend._online_mapper.update.call_args.kwargs,
                             {'pose': (10., 20., 0.), 'timestamp': .12})

    def test_private_capture_requests_calibration_and_does_not_advance_physics(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict('sys.modules', fake_og_modules()):
            backend, _ = self.backend(folder)
            backend._update_online_map()
            backend._render_rgb_views.assert_called_once_with(require_calibration=True)
            backend.og.sim.step.assert_not_called()
            self.assertEqual(backend.steps, 12)

    def test_invalid_private_depth_never_becomes_free_mapper_evidence(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict('sys.modules', fake_og_modules()):
            backend, _ = self.backend(folder)
            for data, _ in backend._sensor_packets.values():
                data['depth_linear'] = Tensor(np.full((9, 9), np.inf))
            backend._update_online_map(rendered=True)
            packets = backend._online_mapper.update.call_args.args[0]
            self.assertTrue(all(not p['returns'] and not p['misses'] for p in packets))

    def test_config_mro_preserves_task_config_and_selects_registered_scene(self):
        original = {'scene': {'type': 'InteractiveTraversableScene', 'scene_model': 'fixture'},
                    'task': {'type': 'BehaviorTask'}}

        class Parent:
            def _config(self, task, gpu, max_steps):
                self.received = task, gpu, max_steps
                return copy.deepcopy(original)

        class Backend(OnlineNavigation, Parent):
            pass

        class ObservedNavigationScene:
            pass

        with patch('manipulation_agent.executors.online_scene.register_online_scene',
                   return_value=ObservedNavigationScene):
            backend = Backend()
            config = backend._config('fixture-task', 2, 123)
        self.assertEqual(backend.received, ('fixture-task', 2, 123))
        self.assertEqual(config['scene']['type'], 'ObservedNavigationScene')
        self.assertEqual(config['scene']['scene_model'], 'fixture')
        self.assertEqual(config['task'], original['task'])
        self.assertEqual(original['scene']['type'], 'InteractiveTraversableScene')

    def test_scene_skips_precomputed_loader_and_refuses_map_access(self):
        from manipulation_agent.executors.online_scene import register_online_scene
        modules = fake_og_modules()
        module = ModuleType('omnigibson.scenes.interactive_traversable_scene')
        register = {}

        class InteractiveTraversableScene:
            def __init_subclass__(cls):
                register[cls.__name__] = cls

            def _load(self):
                raise AssertionError('Precomputed map loader called')

        module.InteractiveTraversableScene = InteractiveTraversableScene
        modules['omnigibson.scenes'] = ModuleType('omnigibson.scenes')
        modules['omnigibson.scenes.interactive_traversable_scene'] = module
        register_online_scene.cache_clear()
        self.addCleanup(register_online_scene.cache_clear)
        with patch.dict('sys.modules', modules):
            scene_class = register_online_scene()
            scene = scene_class()
            scene._load()
            self.assertIs(register['ObservedNavigationScene'], scene_class)
            self.assertTrue(issubclass(scene_class, InteractiveTraversableScene))
            self.assertIs(register_online_scene(), scene_class)
            with self.assertRaisesRegex(RuntimeError, 'Precomputed traversability is disabled'):
                _ = scene.trav_map

    def test_close_releases_mapper_and_tolerates_uninitialized_backend(self):
        OnlineNavigation()._close_online_navigation()
        with tempfile.TemporaryDirectory() as folder:
            backend, _ = self.backend(folder)
            backend._close_online_navigation()
            backend._online_mapper.close.assert_called_once_with()

    def initialization_fixture(self, folder, yaw=0.):
        backend = OnlineNavigation()
        backend.output = Path(folder)
        backend.deadline = object()
        c, s = math.cos(yaw), math.sin(yaw)
        rotation = np.array([[c, -s], [s, c]])
        corners = np.array([[-.4, -.3], [-.4, .3], [.4, -.3], [.4, .3]])
        world = corners @ rotation.T + [10, 20]
        backend.robot = SimpleNamespace(
            aabb=np.array([[9.5, 19.5, .02], [10.5, 20.5, 1.4]]),
            get_position_orientation=lambda: (np.array([10., 20., .02]), None),
            base_footprint_link=SimpleNamespace(collision_boundary_points_world=
                                               np.column_stack((world, np.full(4, .2)))),
            floor_touching_base_links=[])
        backend.torch = SimpleNamespace(linalg=SimpleNamespace(norm=lambda values, dim:
                                                              np.linalg.norm(values, axis=dim)))
        mapper_module = ModuleType('manipulation_agent.executors.cartographer_mapping')
        mapper_module.CartographerMapper = Mock(return_value=SimpleNamespace(close=Mock()))
        return backend, mapper_module

    def test_radius_uses_actual_geometry_and_is_invariant_to_world_yaw(self):
        with tempfile.TemporaryDirectory() as folder:
            for yaw in (0., math.pi / 4, math.pi / 2):
                with self.subTest(yaw=yaw):
                    backend, module = self.initialization_fixture(folder, yaw)
                    with patch.dict('sys.modules', {'manipulation_agent.executors.cartographer_mapping': module}):
                        backend._initialize_online_navigation()
                    self.assertAlmostEqual(backend._online_robot_radius, .5, places=12)
                    self.assertEqual(backend._online_floor_height, .02)
                    self.assertTrue(backend._online_enabled)
                    module.CartographerMapper.assert_called_once()

    def test_invalid_footprint_fails_before_starting_native_worker(self):
        with tempfile.TemporaryDirectory() as folder:
            backend, module = self.initialization_fixture(folder)
            backend.robot.base_footprint_link.collision_boundary_points_world = None
            with patch.dict('sys.modules', {'manipulation_agent.executors.cartographer_mapping': module}):
                with self.assertRaisesRegex(ValueError, 'usable navigation footprint'):
                    backend._initialize_online_navigation()
            module.CartographerMapper.assert_not_called()


if __name__ == '__main__':
    unittest.main()
