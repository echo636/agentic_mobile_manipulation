"""RGB manipulation must not silently move the base toward selected pixels."""
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from manipulation_agent.contracts import SkillError
from manipulation_agent.executors.omnigibson_rgb import RGBBackend


@unittest.skipUnless(importlib.util.find_spec('torch'), 'Requires CPU torch tensors')
class VisualManipulationNavigation(unittest.TestCase):
    def setUp(self):
        import torch
        self.torch = torch
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.b = RGBBackend.__new__(RGBBackend)
        self.b.output = Path(directory.name)
        self.b.torch = torch
        self.b.steps = 0
        self.b.demo_motion = False
        self.b.ideal_carry = True
        self.b.high_level_harness = False
        self.b.robot = SimpleNamespace(get_position_orientation=lambda: (torch.zeros(3), None))
        self.b.env = SimpleNamespace(scene=SimpleNamespace(objects=[]))
        self.obj = SimpleNamespace(states={}, fixed_base=False, category='carton_of_milk')
        self.b._ground = Mock(return_value=(self.obj, torch.tensor([3., 0., 1.]), {}))
        self.b._get_held = Mock(return_value=None)
        self.b._navigate = Mock(side_effect=AssertionError('Manipulation initiated navigation'))
        self.b._ideal_grasp = Mock(return_value={'primitive': 'grasp'})
        self.b._ideal_state_action = Mock(return_value={'primitive': 'open'})
        self.b._checked_place_inside = Mock(return_value={'primitive': 'place_inside'})
        states = ModuleType('omnigibson.object_states')
        states.Open, states.Inside = type('Open', (), {}), type('Inside', (), {})
        parent = ModuleType('omnigibson')
        parent.object_states = states
        modules = patch.dict(sys.modules, {'omnigibson': parent, 'omnigibson.object_states': states})
        modules.start()
        self.addCleanup(modules.stop)

    def test_far_manipulations_reject_without_navigation_or_effect(self):
        for primitive in ('grasp', 'open', 'close', 'place_inside'):
            with self.subTest(primitive=primitive):
                self.b._get_held.return_value = object() if primitive == 'place_inside' else None
                with self.assertRaises(SkillError) as error:
                    self.b.execute_visual(primitive, {}, 500)
                self.assertEqual(error.exception.code, 'out_of_reach')
                self.assertFalse(error.exception.changed)
        self.b._navigate.assert_not_called()
        self.b._ideal_grasp.assert_not_called()
        self.b._ideal_state_action.assert_not_called()
        self.b._checked_place_inside.assert_not_called()
        self.assertEqual(self.b.steps, 0)

    def test_nearby_selected_item_can_be_grasped_without_walking(self):
        self.b._ground.return_value = self.obj, self.torch.tensor([.8, 0., 1.]), {}
        result = self.b.execute_visual('grasp', {}, 500)
        self.assertEqual(result['grounded_category'], 'carton_of_milk')
        self.b._ideal_grasp.assert_called_once()
        self.b._navigate.assert_not_called()

    def test_demo_arm_reach_failure_does_not_retry_with_base_navigation(self):
        self.b.demo_motion = True
        self.b._demo_arm = None
        self.b._demo_last_reach_error = .8
        self.b._demo_reach = Mock(return_value=1)
        self.b._ground.return_value = self.obj, self.torch.tensor([.8, 0., 1.]), {}
        with self.assertRaises(SkillError) as error:
            self.b.execute_visual('open', {}, 500)
        self.assertEqual(error.exception.code, 'out_of_reach')
        self.b._navigate.assert_not_called()
        self.b._ideal_state_action.assert_not_called()

    def test_small_floor_bag_is_a_navigation_obstacle(self):
        # Measured grocery bag extents from the live regression. Small XY
        # footprint must not allow a base path through a staged container.
        bag=SimpleNamespace(name='staged_bag',category='paper_bag',fixed_base=False,
            aabb=(self.torch.tensor([6.541291, .947046, -.007193]),
                  self.torch.tensor([6.662579, 1.128772, .341578])))
        self.b._carry_contents=[]
        self.b.env.scene.objects=[bag]
        boxes,names=self.b._dynamic_floor_obstacles(.005)
        self.assertEqual(names,['staged_bag'])
        self.assertEqual(len(boxes),1)
        # Carried objects remain excluded from the walking obstacle map.
        self.b._get_held.return_value=bag
        self.assertEqual(self.b._dynamic_floor_obstacles(.005),([],[]))

    def test_low_object_navigation_is_not_a_floor_waypoint(self):
        self._check_navigation_intent(self.obj, .0692355633, False)

    def test_actual_floor_navigation_remains_a_waypoint(self):
        floor = SimpleNamespace(category='floors')
        self._check_navigation_intent(floor, .005, True)

    def test_elevated_object_navigation_is_not_a_floor_waypoint(self):
        self._check_navigation_intent(self.obj, .7, False)

    def test_object_navigation_endpoint_passes_manipulation_distance_gate(self):
        import math
        from manipulation_agent.executors import gt_navigation
        from manipulation_agent.observations.rig import visible_rig_rays

        class Planned(Exception):
            pass

        point = self.torch.tensor([3.82, 2.03, .02])
        self.b._ground.return_value = self.obj, point, {}
        self.b.robot.get_position_orientation = lambda: (
            self.torch.tensor([0., 2., .005]), None)
        self.b._navigate = RGBBackend._navigate.__get__(self.b)
        self.b.rig_height, self.b.rig_pitch_degrees, self.b.rig_radius = 1.81, 35., .35
        cells = self.torch.ones((100, 160))
        self.b.env.scene.trav_map = SimpleNamespace(
            floor_heights=[0.], map_resolution=.05, _erode_trav_map=lambda value: value)
        self.b._floor_map_with_open_garage_door = Mock(return_value=(cells, {}))
        self.b._dynamic_floor_obstacles = Mock(return_value=([], []))
        self.b._navigation_self_hulls = Mock(return_value=((), {}))
        self.b._approach_visible = lambda xy, point, owner, **kw: bool(visible_rig_rays(
            xy, math.atan2(float(point[1])-xy[1], float(point[0])-xy[0]),
            .005, 1.81, point.tolist(), margin=kw['margin'], radius=.35, pitch_degrees=35))
        real_plan = gt_navigation.plan_navigation

        def inspect(*args, **kwargs):
            plan = real_plan(*args, **kwargs)
            self.assertLessEqual(math.dist(plan.goal, point[:2].tolist()), 1.4)
            raise Planned

        with patch.object(gt_navigation, 'plan_navigation', side_effect=inspect):
            with self.assertRaises(Planned):
                self.b.execute_visual('navigate_to', {}, 500)

    def _check_navigation_intent(self, visible_owner, point_z, expected_floor):
        import importlib
        gt_navigation = importlib.import_module('manipulation_agent.executors.gt_navigation')

        class StopBeforePathSearch(Exception):
            pass

        # Replay the public dispatch and real navigation classification. The
        # ray owner is available only if dispatch actually requests resolution;
        # otherwise navigation receives None, as in the recorded milk case.
        point = self.torch.tensor([1.2, 0., point_z])
        self.b._ground.side_effect = lambda target, require_object=True: (
            visible_owner if require_object else None, point, {})
        self.b._navigate = RGBBackend._navigate.__get__(self.b)
        self.b.rig_height, self.b.rig_pitch_degrees, self.b.rig_radius = 1.81, 35., .35
        self.b.env.scene.trav_map = SimpleNamespace(floor_heights=[0.])
        self.b._floor_map_with_open_garage_door = Mock(side_effect=StopBeforePathSearch)
        with patch.object(gt_navigation, 'navigation_approach_settings',
                          wraps=gt_navigation.navigation_approach_settings) as settings:
            with self.assertRaises(StopBeforePathSearch):
                self.b.execute_visual('navigate_to', {}, 500)
        self.assertEqual(settings.call_args.kwargs['floor_waypoint'], expected_floor)
