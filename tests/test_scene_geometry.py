import copy
import unittest

from manipulation_agent.scene_geometry import CompleteShellAdmission, restore_static_shell


def entry(name, category, room='corridor_0', fixed=True):
    return {'args': {'name': name, 'category': category, 'fixed_base': fixed, 'in_rooms': [room]}}


class OriginalRoomFilter:
    def _should_load_object(self, obj_info, task_metadata):
        args = obj_info['args']
        return args['category'] in {'walls', 'ceilings'} or 'kitchen_0' in args['in_rooms']


class RepairedFilter(CompleteShellAdmission, OriginalRoomFilter):
    not_load_object_categories = None


class SceneGeometryTests(unittest.TestCase):
    def test_explicit_embedded_robot_without_room_metadata_is_loaded(self):
        probe = RepairedFilter()
        robot = {'class_module': 'omnigibson.robots.robot', 'class_name': 'Robot',
                 'args': {'name': 'robot_r1', 'category': 'object', 'in_rooms': []}}
        probe._include_robots = True
        self.assertTrue(probe._should_load_object(robot, {}))
        probe._include_robots = False
        self.assertFalse(probe._should_load_object(robot, {}))

    def test_restored_corridor_floor_survives_the_final_room_filter(self):
        self.assertTrue(RepairedFilter()._should_load_object(entry('floor', 'floors'), {}))

    def test_fixed_windows_survive_outside_task_rooms(self):
        self.assertTrue(RepairedFilter()._should_load_object(entry('window', 'fixed_window'), {}))

    def test_architectural_openings_and_outdoor_shell_are_restored(self):
        data = {'objects_info': {'init_info': {}},
                'state': {'registry': {'object_registry': {}}}}
        full = copy.deepcopy(data)
        for category in ['openable_window', 'rail_fence', 'swimming_pool']:
            full['objects_info']['init_info'][category] = entry(category, category)
            full['state']['registry']['object_registry'][category] = {'pose': category}
        self.assertEqual(set(restore_static_shell(data, full)), set(full['objects_info']['init_info']))
        for obj in data['objects_info']['init_info'].values():
            self.assertTrue(RepairedFilter()._should_load_object(obj, {}))

    def test_furniture_room_filter_is_preserved(self):
        probe = RepairedFilter()
        self.assertFalse(probe._should_load_object(entry('sofa', 'sofa'), {}))
        self.assertTrue(probe._should_load_object(entry('sofa', 'sofa', 'kitchen_0'), {}))
        self.assertFalse(probe._should_load_object(entry('moving_floor', 'floors', fixed=False), {}))

    def test_explicit_shell_blacklist_is_respected(self):
        probe = RepairedFilter()
        probe.not_load_object_categories = ['floors']
        self.assertFalse(probe._should_load_object(entry('floor', 'floors'), {}))

    def test_restore_preserves_existing_state_and_excludes_furniture(self):
        data = {'objects_info': {'init_info': {'existing': entry('existing', 'floors')}},
                'state': {'registry': {'object_registry': {'existing': {'pose': 'partial'}}}}}
        full = copy.deepcopy(data)
        full['state']['registry']['object_registry']['existing'] = {'pose': 'full'}
        for name, cat in [('window', 'fixed_window'), ('floor', 'floors'), ('chair', 'chair')]:
            full['objects_info']['init_info'][name] = entry(name, cat)
            full['state']['registry']['object_registry'][name] = {'pose': name}
        original = copy.deepcopy(full)
        self.assertEqual(set(restore_static_shell(data, full)), {'window', 'floor'})
        self.assertEqual(data['state']['registry']['object_registry']['existing'], {'pose': 'partial'})
        self.assertNotIn('chair', data['objects_info']['init_info'])
        self.assertEqual(restore_static_shell(data, full), [])
        self.assertEqual(full, original)


if __name__ == '__main__':
    unittest.main()
