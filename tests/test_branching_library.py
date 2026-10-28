"""Temporal contract tests; these do not establish simulator feasibility."""
import json
from pathlib import Path
import unittest

from manipulation_agent.tasks.custom_tasks import validate_spec
from manipulation_agent.tasks.long_horizon import TemporalGoalEvaluator

RECIPE = Path(__file__).resolve().parents[1] / 'task_designs/constructed_branching/bedroom_shared_library_branching_v2.json'


class Trace:
    def __init__(self):
        self.spec = json.loads(RECIPE.read_text())
        self.values = dict.fromkeys(self.spec['evaluation']['predicates'], False)
        for expr in self.spec['initial_assertions'].values():
            if isinstance(expr, str):
                self.values[expr] = True
        self.values['hand_empty'] = True
        self.tracker = TemporalGoalEvaluator(self.spec['evaluation'])
        self.sequence = 0
        self.tracker.observe(self.values, 0, env_step=0, initial=True)

    def observe(self):
        self.sequence += 1
        self.tracker.observe(self.values, self.sequence, env_step=self.sequence)

    def place(self, item, target):
        for key, p in self.spec['evaluation']['predicates'].items():
            if p['object'] == item and p['state'] == 'OnTop':
                self.values[key] = p['target'] == target
        self.observe()

    def clean(self, target):
        self.values[target + '_Covered_dust'] = False
        self.observe()

    def phase(self, phase):
        plan = self.spec['reference_plan']
        if phase == 'library':
            for w in plan['clutter']:
                self.place(w['item'], w['destination'])
            self.clean('shared_shelf')
        elif phase == 'close':
            for lamp in plan['lights']:
                self.values[lamp + '_ToggledOn'] = False
                self.observe()
        else:
            rid, action = phase.split('_', 1)
            room = next(r for r in plan['rooms'] if r['id'] == rid)
            if action in ('stage', 'restore'):
                for w in room['work']:
                    self.place(w['item'], w['stage' if action == 'stage' else 'destination'])
            elif action == 'vacuum':
                self.clean(room['floor'])
            elif action == 'wipe':
                self.clean(rid + '_stage')

    def finish(self, order='library_then_a_then_b'):
        for phase in self.spec['reference_plan']['schedules'][order]:
            self.phase(phase)
        return self.tracker.result(self.values)


class BranchingLibraryTests(unittest.TestCase):
    def test_legacy_fixed_furniture_import_without_velocity(self):
        from manipulation_agent.executors.custom_task import imported_state_with_velocities
        source = dict(root_link=dict(pos=[1., 2., .4], ori=[0., 0., 0., 1.]), non_kin={})
        state = imported_state_with_velocities(source)
        self.assertEqual(state['root_link']['lin_vel'], [0., 0., 0.])
        self.assertEqual(state['root_link']['ang_vel'], [0., 0., 0.])
        self.assertNotIn('lin_vel', source['root_link'])
        source['root_link']['lin_vel'] = [0., .1, 0.]
        self.assertEqual(imported_state_with_velocities(source)['root_link']['lin_vel'], [0., .1, 0.])

    def test_contract_and_branch_shape(self):
        spec = json.loads(RECIPE.read_text())
        self.assertEqual(validate_spec(spec)['status'], 'passed')
        graph = spec['evaluation']['milestones']
        self.assertEqual(len(graph), 15)
        self.assertEqual(sum(len(r['after']) for r in graph.values()), 17)
        self.assertEqual(graph['a_restored']['after'], ['a_floor_clean', 'shelf_clean'])
        self.assertEqual(graph['b_restored']['after'], ['b_floor_clean', 'shelf_clean'])

    def test_three_distinct_interleavings(self):
        spec = json.loads(RECIPE.read_text())
        for order in spec['acceptance_cases']['reference_orders']:
            with self.subTest(order=order):
                result = Trace().finish(order)
                self.assertTrue(result['task_success'], result['public_constraint_violations'])

    def test_toys_need_own_floor_but_not_shared_shelf(self):
        t = Trace()
        t.phase('a_stage'); t.phase('a_vacuum')
        t.place('a_item00', 'a_destination')
        self.assertFalse(t.tracker.violations)
        self.assertNotIn('a_restored', t.tracker.milestones)

    def test_early_book_is_not_repaired_by_later_cleaning(self):
        t = Trace()
        t.phase('a_stage'); t.phase('a_vacuum')
        t.place('a_item01', 'shared_shelf')
        t.place('a_item01', 'a_stage')
        # Complete the remaining legal work without re-running A's staging.
        for phase in ['library', 'a_restore', 'a_wipe', 'b_stage', 'b_vacuum', 'b_restore', 'b_wipe', 'close']:
            t.phase(phase)
        result = t.tracker.result(t.values)
        self.assertTrue(all(result['terminal_conditions'].values()))
        self.assertFalse(result['task_success'])
        self.assertIn('shelf_clean_before_books', {v['id'] for v in t.tracker.violations})

    def test_current_dirty_shelf_rejected_after_clean_history(self):
        t = Trace(); t.phase('library'); t.phase('a_stage'); t.phase('a_vacuum')
        t.values['shared_shelf_Covered_dust'] = True; t.observe()
        t.place('a_item01', 'shared_shelf')
        self.assertIn('shelf_currently_clean_under_books', {v['id'] for v in t.tracker.violations})

    def test_initial_and_current_staging_required_before_vacuum(self):
        for remove_one in [False, True]:
            t = Trace()
            if remove_one:
                t.phase('a_stage'); t.place('a_item00', 'floor')
            t.clean('floor')
            expected = 'a_keep_staged_until_vacuum' if remove_one else 'a_stage_before_vacuum'
            self.assertIn(expected, {v['id'] for v in t.tracker.violations})

    def test_early_stage_wipe_and_cross_room_shutdown(self):
        t = Trace(); t.clean('a_stage')
        self.assertIn('a_restore_before_stage_wipe', {v['id'] for v in t.tracker.violations})
        t = Trace()
        for phase in ['library', 'a_stage', 'a_vacuum', 'a_restore', 'a_wipe']:
            t.phase(phase)
        t.values['a_lamp0_ToggledOn'] = False; t.observe()
        self.assertIn('a_lamp0_last', {v['id'] for v in t.tracker.violations})

    def test_wrong_room_missing_clutter_and_light_remain_unsolved(self):
        for item, target in [('a_item00', 'b_destination'), ('clutter0', 'shared_shelf')]:
            t = Trace(); t.finish(); t.place(item, target)
            self.assertFalse(t.tracker.result(t.values)['task_success'])
        t = Trace(); t.finish(); t.values['b_lamp1_ToggledOn'] = True; t.observe()
        self.assertFalse(t.tracker.result(t.values)['task_success'])


if __name__ == '__main__':
    unittest.main()
