"""Logical contract regressions; these do not establish physical feasibility."""
import itertools
import json
from pathlib import Path
import unittest

from manipulation_agent.tasks.custom_tasks import load_spec
from manipulation_agent.tasks.long_horizon import TemporalGoalEvaluator


ROOT = Path(__file__).resolve().parents[1] / 'task_designs/candidates'
TOOLS = ('drill', 'pliers', 'flashlight', 'allen_wrench', 'screwdriver')
FOOD = ('hotdog1', 'hotdog2', 'apple1', 'apple2')


def task(name):
    return json.loads((ROOT / (name + '_v1.json')).read_text())


class CandidateContractTests(unittest.TestCase):
    def evaluator(self, name):
        spec = task(name)
        evaluator = TemporalGoalEvaluator(spec['evaluation'])
        values = dict.fromkeys(spec['evaluation']['predicates'], False)
        evaluator.observe(values, 0, env_step=0, initial=True)
        return evaluator, values

    def observe(self, evaluator, values):
        self.assertEqual(set(values), set(evaluator.spec['predicates']))
        sequence = evaluator.last_sequence + 1
        evaluator.observe(values, sequence, env_step=sequence)

    def kit_outbound(self):
        evaluator, values = self.evaluator('workshop_inventory_roundtrip')
        values.update(box_at_source=True, **{x+'_in_box': True for x in TOOLS})
        self.observe(evaluator, values)
        values.update(box_at_source=False, box_on_bench=True)
        self.observe(evaluator, values)
        return evaluator, values

    def kit_inventory(self, evaluator, values):
        values.update(box_open=True, **{x+'_in_box': False for x in TOOLS},
                      **{x+'_on_bench': True for x in TOOLS})
        self.observe(evaluator, values)

    def kit_repack(self, evaluator, values):
        values.update(box_open=False, **{x+'_in_box': True for x in TOOLS},
                      **{x+'_on_bench': False for x in TOOLS})
        self.observe(evaluator, values)

    def kit_return(self, evaluator, values):
        values.update(box_at_source=True, box_on_bench=False,
                      book1_stored=True, book2_stored=True,
                      **{f'can{i}_in_bin': True for i in (1, 2, 3)})
        self.observe(evaluator, values)

    def test_both_candidates_have_complete_public_contracts(self):
        for path in sorted(ROOT.glob('*.json')):
            with self.subTest(path=path):
                spec, result = load_spec(path)
                self.assertEqual(result['status'], 'passed')
                self.assertEqual(spec['status'], 'candidate_physics_unvalidated')
                self.assertFalse(result['physical_feasibility_validated'])

    def test_full_inventory_then_loaded_return_is_accepted(self):
        evaluator, values = self.kit_outbound()
        self.kit_inventory(evaluator, values)
        self.kit_repack(evaluator, values)
        self.kit_return(evaluator, values)
        self.assertTrue(evaluator.result(values)['task_success'])

    def test_same_final_state_without_inventory_is_rejected(self):
        evaluator, values = self.kit_outbound()
        self.kit_return(evaluator, values)
        result = evaluator.result(values)
        self.assertTrue(all(result['terminal_conditions'].values()))
        self.assertFalse(result['historical_conditions']['tools_unpacked'])
        self.assertFalse(result['task_success'])

    def test_one_tool_at_a_time_is_not_complete_inventory(self):
        evaluator, values = self.kit_outbound()
        for name in TOOLS:
            values.update({name+'_in_box': False, name+'_on_bench': True})
            self.observe(evaluator, values)
            values.update({name+'_in_box': True, name+'_on_bench': False})
            self.observe(evaluator, values)
        self.kit_return(evaluator, values)
        self.assertFalse(evaluator.result(values)['historical_conditions']['tools_unpacked'])

    def test_open_return_violation_survives_correct_final_pose(self):
        evaluator, values = self.kit_outbound()
        self.kit_inventory(evaluator, values)
        self.kit_repack(evaluator, values)
        values['box_open'] = True
        evaluator.check_motion(values, {'box'}, env_step=5)
        values['box_open'] = False
        self.kit_return(evaluator, values)
        result = evaluator.result(values)
        self.assertTrue(all(result['terminal_conditions'].values()))
        self.assertTrue(all(result['historical_conditions'].values()))
        self.assertFalse(result['constraint_conditions']['closed_return_transport'])
        self.assertFalse(result['task_success'])

    def meal_delivered(self):
        evaluator, values = self.evaluator('kitchen_service_and_reset')
        values.update(box_on_prep=True, hotdog1_cooked=True, hotdog2_cooked=True,
                      **{x+'_packed': True for x in FOOD})
        self.observe(evaluator, values)
        values.update(box_on_prep=False, lunch_delivered=True)
        self.observe(evaluator, values)
        return evaluator, values

    def meal_servings(self, values, hotdog_order=(1, 2), apple_order=(1, 2)):
        values.update(**{x+'_packed': False for x in FOOD},
                      plate1_set=True, plate2_set=True, fork1_set=True, fork2_set=True,
                      tea_served=True, cookie_stored=True, sandwich_stored=True,
                      board_stored=True, **{f'can{i}_stored': True for i in (1, 2, 3)})
        for kind, order in (('hotdog', hotdog_order), ('apple', apple_order)):
            for index, plate in enumerate(order, 1):
                values[f'{kind}{index}_on_plate{plate}'] = True

    def meal_return(self, evaluator, values):
        values.update(lunch_delivered=False, box_at_storage=True)
        self.observe(evaluator, values)

    def test_food_identity_permutations_are_equally_valid(self):
        for hotdogs, apples in itertools.product(((1, 2), (2, 1)), repeat=2):
            with self.subTest(hotdogs=hotdogs, apples=apples):
                evaluator, values = self.meal_delivered()
                self.meal_servings(values, hotdogs, apples)
                self.observe(evaluator, values)
                self.meal_return(evaluator, values)
                self.assertTrue(evaluator.result(values)['task_success'])

    def test_all_food_on_one_plate_is_not_two_servings(self):
        evaluator, values = self.meal_delivered()
        self.meal_servings(values, (1, 1), (1, 1))
        self.observe(evaluator, values)
        self.meal_return(evaluator, values)
        result = evaluator.result(values)
        self.assertTrue(all(result['terminal_conditions'][x+'_on_plate'] for x in FOOD))
        self.assertFalse(result['terminal_conditions']['two_complete_servings'])
        self.assertFalse(result['historical_conditions']['meal_served'])

    def test_terminal_servings_cannot_replace_packed_delivery(self):
        evaluator, values = self.evaluator('kitchen_service_and_reset')
        values.update(hotdog1_cooked=True, hotdog2_cooked=True)
        self.meal_servings(values)
        self.meal_return(evaluator, values)
        result = evaluator.result(values)
        self.assertTrue(all(result['terminal_conditions'].values()))
        self.assertFalse(result['historical_conditions']['meal_delivered'])
        self.assertFalse(result['task_success'])

    def test_incomplete_carrier_motion_is_a_permanent_violation(self):
        evaluator, values = self.evaluator('kitchen_service_and_reset')
        values.update(box_on_prep=True, hotdog1_cooked=True, hotdog2_cooked=True,
                      **{x+'_packed': True for x in FOOD})
        self.observe(evaluator, values)
        values['apple1_packed'] = False
        evaluator.check_motion(values, {'box'}, env_step=2)
        values.update(apple1_packed=True, box_on_prep=False, lunch_delivered=True)
        self.observe(evaluator, values)
        self.meal_servings(values)
        self.observe(evaluator, values)
        self.meal_return(evaluator, values)
        result = evaluator.result(values)
        self.assertTrue(all(result['terminal_conditions'].values()))
        self.assertFalse(result['constraint_conditions']['complete_meal_transport'])
        self.assertFalse(result['task_success'])


if __name__ == '__main__':
    unittest.main()
