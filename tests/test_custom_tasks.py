import copy
import json
from pathlib import Path
import tempfile
import unittest

from manipulation_agent.tasks.custom_tasks import load_spec, sha256, validate_spec
from manipulation_agent.tasks.long_horizon import TemporalGoalEvaluator


ROOT = Path(__file__).resolve().parents[1]


def spec(name):
    return json.loads((ROOT / 'task_designs/evaluation_v2' / (name + '_v2.json')).read_text())


class PublicContractTests(unittest.TestCase):
    def test_imported_fixture_scale_is_explicit_and_physically_valid(self):
        task = spec('movie_night_and_restore')
        item = next(row for row in task['asset_imports'] if row['name'] == 'lh_bin')
        item['scale'] = [1.5, 1.5, 1.5]
        self.assertEqual(validate_spec(task)['status'], 'passed')
        for scale in (1.5, [], [1, 1], [1, 1, 1, 1], [True, 1, 1],
                      [0, 1, 1], [-1, 1, 1], [float('nan'), 1, 1], [1, float('inf'), 1]):
            with self.subTest(scale=scale):
                item['scale'] = scale
                with self.assertRaisesRegex(ValueError, 'asset scale'):
                    validate_spec(task)

    def test_all_published_v2_requirements_cover_exact_scoring_contract(self):
        for path in sorted((ROOT / 'task_designs/evaluation_v2').glob('*.json')):
            with self.subTest(path=path):
                _, result = load_spec(path)
                self.assertEqual(result['status'], 'passed')
                self.assertFalse(result['physical_feasibility_validated'])

    def test_unadvertised_goal_and_unscored_requirement_are_rejected(self):
        original = spec('warm_lunch_and_restore')
        hidden = copy.deepcopy(original)
        hidden['evaluation']['terminal']['unexpected'] = 'tea_served'
        with self.assertRaisesRegex(ValueError, 'unmapped'):
            validate_spec(hidden)
        extra = copy.deepcopy(original)
        extra['requirements'].append({'id': 'unscored', 'text': 'Clean the floor.'})
        extra['public_context'].append('Clean the floor.')
        with self.assertRaisesRegex(ValueError, 'Unscored'):
            validate_spec(extra)

    def test_public_text_cannot_diverge_from_private_mapping(self):
        task = spec('workshop_setup_and_return')
        task['public_context'][0] = 'Just put the tools on the workbench.'
        with self.assertRaisesRegex(ValueError, 'verbatim'):
            validate_spec(task)

    def test_scene_binding_is_verified_and_recipe_is_not_frozen(self):
        task = spec('warm_lunch_and_restore')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'task.json'
            scene = Path(directory) / 'scene.json'
            scene.write_text('{"example": 1}')
            with self.assertRaisesRegex(ValueError, 'no frozen'):
                validate_spec(task, spec_path=path, require_frozen=True)
            task['frozen_instance'] = {'path': 'scene.json', 'sha256': sha256(scene)}
            validate_spec(task, spec_path=path, require_frozen=True)
            scene.write_text('{"example": 2}')
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                validate_spec(task, spec_path=path, require_frozen=True)

    def test_duplicate_constraints_cannot_shrink_score_denominator(self):
        task = spec('movie_night_and_restore')
        task['evaluation']['motion_guards'].append(copy.deepcopy(task['evaluation']['motion_guards'][0]))
        with self.assertRaisesRegex(ValueError, 'unique'):
            validate_spec(task)

    def test_acceptance_must_have_real_restore_and_action_budgets(self):
        task = spec('warm_lunch_and_restore')
        task['acceptance_cases']['fresh_restore_repetitions'] = 1
        with self.assertRaisesRegex(ValueError, 'restore count'):
            validate_spec(task)
        task['acceptance_cases']['fresh_restore_repetitions'] = 2
        task['acceptance_cases']['max_reference_action_attempts'] = True
        with self.assertRaisesRegex(ValueError, 'budget'):
            validate_spec(task)


class RealTaskScoringTests(unittest.TestCase):
    def movie(self):
        evaluation = spec('movie_night_and_restore')['evaluation']
        evaluator = TemporalGoalEvaluator(evaluation)
        values = dict.fromkeys(evaluation['predicates'], False)
        evaluator.observe(values, 0, env_step=0, initial=True)
        values.update(cooked_popcorn=True, bag_in_microwave=True, microwave_on=True)
        evaluator.observe(values, 1, env_step=10)
        values['microwave_on'] = False
        evaluator.observe(values, 2, env_step=20)
        return evaluator, values

    def test_reenergizing_after_off_does_not_authorize_hot_removal(self):
        evaluator, values = self.movie()
        self.assertIn('microwave_off_before_removal', evaluator.milestones)
        values['microwave_on'] = True
        evaluator.observe(values, 3, env_step=30)
        values['bag_in_microwave'] = False
        evaluator.check_motion(values, {'bag'}, env_step=31)
        evaluator.observe(values, 4, env_step=31)
        values.update(microwave_on=False, bag_on_table=True)
        evaluator.observe(values, 5, env_step=40)
        self.assertFalse(evaluator.result(values)['constraint_conditions']['power_off_before_cooked_bag_removal'])

    def test_off_removal_and_later_arrival_are_legal(self):
        evaluator, values = self.movie()
        values['bag_in_microwave'] = False
        evaluator.check_motion(values, {'bag'}, env_step=30)
        evaluator.observe(values, 3, env_step=30)
        values['bag_on_table'] = True
        evaluator.observe(values, 4, env_step=40)
        self.assertIn('popcorn_removed', evaluator.milestones)
        self.assertIn('popcorn_delivered', evaluator.milestones)
        self.assertTrue(all(evaluator.result(values)['constraint_conditions'].values()))

    def test_partially_cooked_popcorn_does_not_count_as_finished_food(self):
        evaluator, values = self.movie()
        values.update(raw_popcorn_present=True, bag_on_table=True)
        self.assertFalse(evaluator.result(values)['terminal_conditions']['popcorn_served'])

    def test_lunch_clearance_is_independent_of_other_final_targets(self):
        evaluation = spec('warm_lunch_and_restore')['evaluation']
        evaluator = TemporalGoalEvaluator(evaluation)
        values = dict.fromkeys(evaluation['predicates'], False)
        # Two geometrically related support surfaces may both report OnTop.
        # Destination predicates alone must not mask a nonempty prep counter.
        values.update(tea_served=True, tea_on_prep=True)
        result = evaluator.result(values)
        self.assertTrue(result['terminal_conditions']['tea_served'])
        self.assertFalse(result['terminal_conditions']['prep_area_clear'])

    def test_final_workshop_snapshot_cannot_replace_loaded_transport(self):
        evaluation = spec('workshop_setup_and_return')['evaluation']
        evaluator = TemporalGoalEvaluator(evaluation)
        values = dict.fromkeys(evaluation['predicates'], False)
        values.update({key: True for key in values if key.endswith(('_on_bench', '_in_bin'))})
        values.update(box_at_source=True, book1_stored=True, book2_stored=True)
        evaluator.observe(values, 0, env_step=0, initial=True)
        evaluator.observe(values, 1, env_step=1)
        result = evaluator.result(values)
        self.assertTrue(all(result['terminal_conditions'].values()))
        self.assertFalse(result['task_success'])
        self.assertFalse(result['historical_conditions']['loaded_box_delivered'])


if __name__ == '__main__':
    unittest.main()
