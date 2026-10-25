"""The planning task is judged from high-level effects, not particle contacts."""
import unittest
import sys
import types
from unittest.mock import patch

from manipulation_agent.contracts import SkillError
from manipulation_agent.high_level_hot_drink import HighLevelHotDrink
from manipulation_agent.executors.omnigibson_rgb import RGBBackend
from manipulation_agent.vision_policy import system_prompt


class HighLevelHotDrinkTests(unittest.TestCase):
    def test_initial_cabinet_state_matches_pinned_scene_instance(self):
        task = HighLevelHotDrink()
        self.assertIn('cabinet', task.open)
        self.assertNotIn('refrigerator', task.open)
        task.apply('grasp', 'kettle')
        self.assertEqual(task.held, 'kettle')

    def test_dependencies_and_complete_task(self):
        task = HighLevelHotDrink()
        with self.assertRaises(SkillError):
            task.apply('grasp', 'water_bottle')
        task.apply('open', 'refrigerator')
        task.apply('grasp', 'water_bottle')
        with self.assertRaises(SkillError):
            task.apply('open', 'cabinet')
        task.apply('place_on_top', 'countertop')
        task.apply('open', 'cabinet')
        task.apply('grasp', 'kettle')
        task.apply('place_on_top', 'countertop')
        task.apply('grasp', 'water_bottle')
        task.apply('pour', 'kettle')
        task.apply('place_on_top', 'countertop')
        task.apply('grasp', 'kettle')
        task.apply('place_near_heating_element', 'stove')
        task.apply('boil', 'stove')
        self.assertTrue(task.evaluate()['semantic_goals']['water_boiled'])
        task.apply('grasp', 'kettle')
        task.apply('pour', 'mug')
        task.apply('place_on_top', 'countertop')
        for obj, support in (
            ('place_mat', 'coffee_table'), ('tray', 'place_mat'),
            ('dinner_napkin', 'place_mat'), ('plate', 'tray'),
            ('teaspoon', 'dinner_napkin'), ('sugar_cookie', 'plate'),
            ('honey_jar', 'tray'), ('mug', 'place_mat')):
            task.apply('grasp', obj)
            task.apply('place_on_top', support)
        result = task.evaluate()
        self.assertTrue(result['task_success'])
        self.assertEqual(result['goal_satisfaction_fraction'], 1)
        self.assertEqual(task.descendants('tray'), [
            ('plate', 'tray'), ('honey_jar', 'tray'), ('sugar_cookie', 'plate')])

    def test_boil_requires_water_filled_kettle_on_stove(self):
        task = HighLevelHotDrink()
        with self.assertRaises(SkillError):
            task.apply('boil', 'stove')
        task.apply('wait', seconds=60)
        self.assertFalse(task.evaluate()['semantic_goals']['water_boiled'])

    def test_wait_does_not_boil_even_with_heat_setup(self):
        task = HighLevelHotDrink()
        task.support['kettle'] = 'stove'
        task.water['kettle'] = 'water'
        task.apply('toggle_on', 'stove')
        task.apply('wait', seconds=60)
        self.assertFalse(task.evaluate()['semantic_goals']['water_boiled'])
        task.apply('boil', 'stove')
        self.assertTrue(task.evaluate()['semantic_goals']['water_boiled'])

    def test_boiling_can_target_visible_kettle(self):
        task = HighLevelHotDrink()
        task.support['kettle'] = 'stove'
        task.water['kettle'] = 'water'
        task.apply('boil', 'kettle')
        self.assertTrue(task.evaluate()['semantic_goals']['water_boiled'])

    def test_backend_final_evaluation_uses_high_level_protocol(self):
        backend = RGBBackend.__new__(RGBBackend)
        backend.high_level_harness = True
        backend.high_level_state = HighLevelHotDrink()
        backend.mode = 'rgb_semantic_magic'
        backend.demo_motion = False
        result = backend.evaluate()
        self.assertEqual(result['protocol'], 'high_level_hot_drink_state_v1')
        self.assertIsNone(result['official_task_success'])
        self.assertIn('semantic_goals', result)

    def test_semantic_prompt_does_not_supply_task_dependencies(self):
        prompt = system_prompt('semantic').lower()
        self.assertNotIn('heat source', prompt)
        self.assertNotIn('to transfer liquid', prompt)
        self.assertNotIn('to carry something', prompt)
        self.assertIn('choose each action, target and order yourself', prompt)

    def test_cup_on_kitchen_tray_is_not_served(self):
        task=HighLevelHotDrink()
        task.open.add('cabinet')
        task.apply('grasp','mug')
        task.apply('place_on_top','tray')
        self.assertFalse(task.evaluate()['semantic_goals']['mug_served'])

    def test_nested_tray_payload_uses_semantic_relations_not_contact(self):
        state = HighLevelHotDrink()
        state.support.update({'mug': 'tray', 'plate': 'tray',
                              'sugar_cookie': 'plate', 'dinner_napkin': 'tray'})
        objects = {name: types.SimpleNamespace(
            name=name, get_position_orientation=lambda index=i: (index, 'upright'))
            for i, name in enumerate(('tray', 'mug', 'plate', 'sugar_cookie', 'dinner_napkin'))}
        backend = RGBBackend.__new__(RGBBackend)
        backend.high_level_state = state
        transform = types.ModuleType('omnigibson.utils.transform_utils')
        transform.relative_pose_transform = lambda child_pos, quat, root_pos, root_quat: (
            child_pos - root_pos, quat)
        utils = types.ModuleType('omnigibson.utils')
        utils.transform_utils = transform
        with patch.dict(sys.modules, {'omnigibson': types.ModuleType('omnigibson'),
                                      'omnigibson.utils': utils,
                                      'omnigibson.utils.transform_utils': transform}):
            edges, contents = backend._high_level_payload_snapshot('tray', objects['tray'], objects)
        self.assertEqual({child.name for child, _, _ in edges},
                         {'mug', 'plate', 'sugar_cookie', 'dinner_napkin'})
        self.assertEqual({child.name for child, _ in contents},
                         {'mug', 'plate', 'sugar_cookie', 'dinner_napkin'})


if __name__ == '__main__':
    unittest.main()
