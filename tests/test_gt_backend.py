"""Default GT wiring with CPU sensor/actuator fixtures, not simulator acceptance."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from manipulation_agent.contracts import SkillError
from manipulation_agent.deadline import EpisodeDeadline
from manipulation_agent.executors.omnigibson_rgb import RGBBackend
from manipulation_agent.omnigibson_backend import OmniGibsonBackend
from manipulation_agent.observations.rig import DIRECTIONS
from manipulation_agent.records import Recorder
from manipulation_agent.vision_harness import VisionHarness


class Tensor:
    """Only the tensor operations used by capture and GT map conversion."""
    def __init__(self, value): self.value = np.asarray(value)
    def clone(self): return Tensor(self.value.copy())
    def detach(self): return self
    def cpu(self): return self
    def numpy(self): return self.value
    def tolist(self): return self.value.tolist()
    def __getitem__(self, key):
        value = self.value[key]
        return value.item() if np.ndim(value) == 0 else Tensor(value)


class GTBackendTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.output = Path(folder.name)/'episode'

    def backend(self, *, blocked=False):
        self.output.mkdir(exist_ok=True)
        b = RGBBackend.__new__(RGBBackend)
        b.output = self.output
        b.steps = b.capture_index = 0
        b.navigation_distance = 0.
        b.deadline = EpisodeDeadline()
        b.ideal_carry = True
        b.record_video = False
        b.video = None
        b.video_render_stride, b.video_render_flushes = 2, 4
        b.image_size, b.rgb_jpeg_quality, b.rig_radius, b.rig_height = 8, 92, 0., 1.8
        b.rig_pitch_degrees=20.
        b.image_files, b.current_frames = {}, {}
        b._position = Tensor([-1., 0., 0.])
        b._orientation = Tensor([0., 0., 0., 1.])
        b.robot = SimpleNamespace(
            get_position_orientation=lambda: (b._position, b._orientation),
            get_joint_positions=lambda: Tensor([0., 0.]), sensors={})
        b.torch = SimpleNamespace(equal=lambda x, y: np.array_equal(x.value, y.value))
        b.og = SimpleNamespace(sim=SimpleNamespace(get_sim_step_dt=lambda: 1/30))
        occupancy = np.full((60, 60), 255, dtype=np.uint8)
        occupancy[:, 30] = 0
        if not blocked:
            occupancy[35:41, 30] = 255  # Route must go through this doorway.
        trav = SimpleNamespace(floor_heights=[0.], map_resolution=.1,
                               floor_map=[Tensor(occupancy)], _erode_trav_map=lambda x: x)
        b.env = SimpleNamespace(scene=SimpleNamespace(trav_map=trav))
        b._navigation_self_hulls = lambda: ((), {'hulls_prepared': 0})
        b._approach_visible = lambda xy, point, selected, **kwargs: xy[0] >= .5
        b._ground = Mock(return_value=(None, Tensor([1., 0., 0.]),
                                       {'object': 'PRIVATE_OBJECT', 'world_point': [1., 0., 0.]}))
        b.rig = {v: SimpleNamespace(name='camera_'+v,
                 get_position_orientation=lambda: (b._position, b._orientation)) for v in DIRECTIONS}
        b._sensor_intrinsics = {v: Tensor([[4., 0., 4.], [0., 4., 4.], [0., 0., 1.]]) for v in DIRECTIONS}
        b._sensor_packets = {v: ({'depth_linear': Tensor(np.ones((8, 8)))}, {}) for v in DIRECTIONS}
        b._render_rgb_views = Mock(side_effect=lambda **_: {
            v: np.full((8, 8, 3), b.steps*10+i, dtype=np.uint8) for i, v in enumerate(DIRECTIONS)})
        b._video_frame = Mock()
        # Any accidental online hook must fail instead of silently running.
        b._initialize_online_navigation = Mock(side_effect=AssertionError('mapper initialized'))
        b._update_online_map = Mock(side_effect=AssertionError('mapper updated'))
        b._close_online_navigation = Mock(side_effect=AssertionError('mapper closed'))
        def execute(grid, plan, max_steps):
            self.assertTrue(all(grid.segment_free(a, c) for a, c in zip(plan.points, plan.points[1:])))
            b._position = Tensor([*plan.goal, 0.])
            b.steps += 3
            return {'actual_path_distance_m': plan.geodesic_m, 'nav_status': 'reached', 'steps': 3}
        b._execute_gt_plan = Mock(side_effect=execute)
        def turn(degrees, max_steps):
            b.steps += 1
            return {'steps': 1}
        b._turn = turn
        return b

    def test_default_scene_config_and_cleanup_do_not_enter_online_hooks(self):
        b = self.backend()
        original = {'scene': {'type': 'InteractiveTraversableScene', 'scene_model': 'fixture'}}
        with patch.object(OmniGibsonBackend, '_config', return_value=copy.deepcopy(original)), \
             patch('manipulation_agent.executors.online_scene.register_online_scene',
                   side_effect=AssertionError('online scene selected')):
            self.assertEqual(b._config('fixture', 0, 100), original)
        with patch.object(OmniGibsonBackend, 'close') as close:
            b.close()
            close.assert_called_once_with()
        b._close_online_navigation.assert_not_called()

    def test_static_grid_plans_through_doorway_and_records_private_provenance(self):
        b = self.backend()
        target = SimpleNamespace(get_position_orientation=lambda: (Tensor([1., 0., 1.]), None))
        result = b._navigate(target, 700)
        grid, plan, limit = b._execute_gt_plan.call_args.args
        self.assertFalse(grid.segment_free((-1., 0.), plan.goal))
        self.assertGreater(plan.geodesic_m, 1.5)
        self.assertEqual(limit, 700)
        self.assertEqual(result['strategy'], 'jinkai_visual_point_gt_grid_v1')
        self.assertFalse(result['dynamic_collision_check'])
        record = json.loads((self.output/'navigation_plans.jsonl').read_text())
        self.assertEqual(record['audience'], 'executor_private')
        self.assertEqual(record['collision_substrate'], 'static_eroded_grid')
        self.assertTrue(record['precomputed_walkability'])
        self.assertFalse(record['online_mapping'])
        b._update_online_map.assert_not_called()

    def test_public_navigation_checks_own_silhouette_but_selected_pixel_reach_may_cross_it(self):
        b=self.backend()
        target=SimpleNamespace(get_position_orientation=lambda:(Tensor([1.,0.,1.]),None))
        seen=[]
        b._navigation_self_hulls=lambda:(('arm_hull',),{'hulls_prepared':1})
        b._approach_visible=lambda xy,point,selected,**kw:(seen.append(kw['self_hulls']) or xy[0]>=.5)
        b._navigate(target,700)
        self.assertIn(('arm_hull',),seen)
        seen.clear()
        b._navigate(target,700,for_manipulation=True)
        self.assertTrue(seen)
        self.assertTrue(all(hulls==() for hulls in seen))

    def test_disconnected_static_grid_fails_before_any_actuator_or_mapper(self):
        b = self.backend(blocked=True)
        target = SimpleNamespace(get_position_orientation=lambda: (Tensor([1., 0., 0.]), None))
        with self.assertRaises(SkillError) as error:
            b._navigate(target, 700)
        self.assertEqual(error.exception.code, 'navigation_unreachable')
        b._execute_gt_plan.assert_not_called()
        b._update_online_map.assert_not_called()
        self.assertEqual(b.steps, 0)

    def test_default_gt_action_keeps_initialize_four_rgb_and_private_boundary(self):
        recorder = Recorder(self.output, {'backend': 'cpu_gt_fixture', 'observation_mode': 'rgb_only'})
        b = self.backend()
        with patch.object(OmniGibsonBackend, 'provenance', return_value={}):
            h = VisionHarness(b, recorder)
        self.assertTrue(recorder.run['backend']['navigation']['precomputed_walkability'])
        self.assertFalse(recorder.run['backend']['navigation']['model_visible_gt'])
        initial = h.call('initialize', {}, 'initial')
        self.assertTrue(initial['initialized'])
        self.assertEqual(b._render_rgb_views.call_count, 1)
        self.assertEqual(initial['observation']['capture']['capture_id'], 'capture-00001')
        self.assertEqual(set(h.catalog), {'initialize', 'look', 'act', 'finish', 'list_skills', 'read_skill'})
        first_image = initial['observation']['images'][1]
        target = {'image_ref': first_image['image_ref'], 'point': [.5, .5]}
        action = h.call('act', {'primitive': 'navigate_to', 'target': target, 'revision': 0}, 'navigate')
        self.assertTrue(action['ok'], action)
        b._ground.assert_called_once_with(target, require_object=False)
        looked = h.call('look', {'yaw_degrees': 45, 'revision': 1}, 'look')
        self.assertTrue(looked['ok'], looked)
        for result, revision in [(initial, 0), (action, 1), (looked, 2)]:
            observation = result['observation']
            self.assertEqual(observation['revision'], revision)
            self.assertEqual([x['view'] for x in observation['images']], list(DIRECTIONS))
            self.assertEqual(observation['capture']['capture_id'], f'capture-{revision+1:05d}')
            for frame in observation['images']:
                data, _ = h.image_bytes(frame['image_ref'])
                self.assertEqual(hashlib.sha256(data).hexdigest(), frame['sha256'])
            public = json.dumps(result)
            for private in ['PRIVATE_OBJECT', 'world_point', 'static_eroded_grid', 'planned_path_distance_m', 'depth_linear']:
                self.assertNotIn(private, public)
        self.assertEqual(b._render_rgb_views.call_count, 3)
        self.assertEqual(len(h.surround.jobs), 0)
        b._update_online_map.assert_not_called()
        audits = [json.loads(line) for line in (self.output/'observation_capture_audit.jsonl').read_text().splitlines()]
        self.assertTrue(all(row['no_robot_motion'] for row in audits))
        self.assertEqual(len(list((self.output/'executor_frames').glob('*.npz'))), 12)


if __name__ == '__main__':
    unittest.main()
