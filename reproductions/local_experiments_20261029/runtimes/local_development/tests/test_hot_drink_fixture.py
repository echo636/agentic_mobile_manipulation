"""The authored hot-drink instance must keep water and visible assets aligned."""
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1] / 'tasks/prepare_and_serve_hot_drink'
SCENE = ROOT / 'scene/house_single_floor/json'


class HotDrinkFixtureTests(unittest.TestCase):
    def test_real_tableware_and_initial_water_alignment(self):
        template = json.loads((SCENE / 'house_single_floor_task_prepare_and_serve_hot_drink_0_0_template.json').read_text())
        instance = json.loads((SCENE / 'house_single_floor_task_prepare_and_serve_hot_drink_instances'
                               / 'house_single_floor_task_prepare_and_serve_hot_drink_0_301_template-tro_state.json').read_text())
        names = template['metadata']['task']['inst_to_name']
        assets = template['objects_info']['init_info']
        for key, category in [('dinner_napkin.n.01_1', 'dinner_napkin'),
                              ('place_mat.n.01_1', 'place_mat')]:
            self.assertEqual(assets[names[key]]['args']['category'], category)
            self.assertIn(key, instance)
        bottle = 'water_bottle.n.01_1'
        initial = template['state']['registry']['object_registry'][names[bottle]]['root_link']
        sampled = instance[bottle]['root_link']
        self.assertEqual(sampled['pos'], initial['pos'])
        self.assertEqual(sampled['ori'], initial['ori'])
        self.assertGreater(template['state']['registry']['system_registry']['water']
                           ['instancer_particle_counts'][0], 0)


if __name__ == '__main__':
    unittest.main()
