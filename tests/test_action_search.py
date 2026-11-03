"""Automatic plan composition: no supplied alternative graphs or sequences."""
import json
from pathlib import Path
import tempfile
import unittest

from manipulation_agent.agent.action_search import search_actions
from manipulation_agent.agent.task_notes import TaskNotes


def op(key, requires, effects, at='bench', seconds=1, **extra):
    return dict(id=key, description=key, at=at, seconds=seconds,
                requires=requires, effects=effects, check='Inspect actual result', **extra)


def document(state, conditions, operators, **planning):
    return dict(format='goal-tree-v2', root_id='root',
        goals=[dict(id='root', parent_id=None, status='active'),
               *[dict(id=k, parent_id='root', goal=k, status='pending', condition=v)
                 for k, v in conditions.items()]],
        planning=dict(location='bench', state=state, **planning),
        execution=dict(operators=operators, operations=[]))


class ActionSearchTests(unittest.TestCase):
    def test_feedback_names_unmodeled_results_without_inventing_work(self):
        doc = document({'a': 'raw', 'hand': 'empty', 'ready': 'yes'},
            {'a': {'a': 'cooked'}, 'b': {'b': 'ready'}, 'done': {'ready': 'yes'},
             'device': {'heat': 'on'}}, [
                op('prepare-a', {'a': 'raw'}, {'a': 'diced'}),
                op('finish-b', {'tool': 'held'}, {'b': 'ready'}),
                op('heat', {'hand': 'empty'}, {}, setup={'heat': 'on'})])
        with tempfile.TemporaryDirectory() as tmp:
            notes = TaskNotes(Path(tmp))
            notes.write(json.dumps(doc), 0, request_id='x', revision=0, agent_step=0)
            advice = notes.planning()
            self.assertEqual(advice['status'], 'partial')
            self.assertEqual(advice['comparison']['missing_producers'],
                             [dict(goal_id='a', condition={'a': 'cooked'})])
            self.assertIn('b', advice['coverage']['unplanned'])
            self.assertIn(['finish-b', 'tool', 'held'], advice['comparison']['gaps']['required_states'])
            self.assertEqual(json.loads(notes.read()['content']), doc)

    def test_equal_cost_branches_reach_a_complete_plan_within_search_budget(self):
        keys = [str(i) for i in range(18)]
        doc = document({k: 'raw' for k in keys}, {k: {k: 'ready'} for k in keys},
                       [op('prepare-'+k, {k: 'raw'}, {k: 'ready'}) for k in keys])
        variants, search = search_actions(doc, max_states=64)
        self.assertTrue(variants[0]['complete'])
        self.assertEqual(variants[0]['estimated_seconds'], 18)
        self.assertEqual(len(variants[0]['path']), 18)
        self.assertTrue(search['search_truncated'])
        self.assertFalse(search['optimal_in_declared_model'])

        doc['goals'].append(dict(id='unknown', parent_id='root', status='pending',
                                 condition={'unlocated': 'ready'}))
        variants, search = search_actions(doc, max_states=64)
        self.assertEqual(variants[0]['covered'], set(keys))
        self.assertEqual(variants[0]['estimated_seconds'], 18)
        self.assertFalse(variants[0]['complete'])
        self.assertFalse(search['optimal_in_declared_model'])
        self.assertEqual(search['missing_producers'],
                         [dict(goal_id='unknown', condition={'unlocated': 'ready'})])

    def test_composes_shared_tool_work_and_materializes_selection(self):
        operators = [op('take-tool', {'hand': 'empty'}, {'hand': 'tool'}),
            op('stage-tool', {'hand': 'tool'}, {'hand': 'empty'}),
            *[op('process-'+k, {'hand': 'tool', k: 'raw'}, {k: 'processed'}) for k in ['a', 'b']],
            *[op('arrange-'+k, {'hand': 'empty', k: 'processed'}, {k: 'finished'}) for k in ['a', 'b']]]
        doc = document({'hand': 'empty', 'a': 'raw', 'b': 'raw'},
                       {k: {k: 'finished'} for k in ['a', 'b']}, operators)
        variants, search = search_actions(doc)
        order = [s['method_id'] for s in variants[0]['path']]
        self.assertEqual(order.count('take-tool'), 1)
        self.assertEqual(order.count('stage-tool'), 1)
        self.assertEqual(variants[0]['estimated_seconds'], 6)
        self.assertTrue(search['optimal_in_declared_model'])
        self.assertFalse(any(search['gaps'].values()))
        graph = variants[0]['operations']
        cuts = [n for n in graph if n['operator_id'].startswith('process-')]
        self.assertEqual(cuts[0]['depends_on'], cuts[1]['depends_on'])
        with tempfile.TemporaryDirectory() as tmp:
            notes = TaskNotes(Path(tmp));meta = dict(request_id='x', revision=0, agent_step=0)
            notes.write(json.dumps(doc), 0, **meta)
            advice = notes.planning()
            notes.select(json.dumps(dict(plan_id=advice['plan_id'], operation_id=advice['next']['operation_id'],
                steps=['Execute visible selected operation'], next_check='Tool held')), 1, **meta)
            self.assertEqual(notes.planning()['selected_operation']['predicted_effects'], {'hand': 'tool'})
            notes.record_action('act', {'primitive': 'grasp'}, {'ok': True, 'runtime': {'tool_seconds': 2}}, 1)
            notes.update(json.dumps({'planning': {'state': {'hand': 'tool', 'a': 'raw', 'b': 'raw'}}}), 2, **meta)
            self.assertEqual(notes.planning()['previous_execution']['action_attempts'], 1)
            self.assertNotEqual(notes.planning()['next']['operator_id'], 'take-tool')
            self.assertTrue(all(g['status'] != 'done' for g in json.loads(notes.read()['content'])['goals']))

    def test_discovers_carrier_combination_and_rejects_it_when_travel_is_short(self):
        operators = []
        for k in ['a', 'b']:
            operators.extend([
                op('pick-'+k, {'hand': 'empty', k: 'bench'}, {'hand': k, k: 'held'}),
                op('put-'+k, {'hand': k, k: 'held'}, {'hand': 'empty', k: 'guest'}, at='guest'),
                op('load-'+k, {'hand': 'empty', k: 'bench', 'tray': 'bench'}, {k: 'tray'}, seconds=3),
                op('unload-'+k, {'hand': 'empty', k: 'tray', 'tray': 'guest'}, {k: 'guest'}, at='guest', seconds=3)])
        operators.extend([op('lift-tray', {'hand': 'empty', 'tray': 'bench'}, {'hand': 'tray', 'tray': 'held'}, seconds=2),
            op('stage-tray', {'hand': 'tray'}, {'hand': 'empty', 'tray': 'guest'}, at='guest', seconds=2)])
        doc = document({'hand': 'empty', 'a': 'bench', 'b': 'bench', 'tray': 'bench'},
            {k: {k: 'guest'} for k in ['a', 'b']}, operators,
            travel=[dict(source=a, destination=b, seconds=100) for a, b in [('bench', 'guest'), ('guest', 'bench')]])
        variants, _ = search_actions(doc)
        self.assertEqual(variants[0]['estimated_seconds'], 116)
        self.assertIn('lift-tray', [s['method_id'] for s in variants[0]['path']])
        for edge in doc['planning']['travel']:
            edge['seconds'] = 1
        variants, _ = search_actions(doc)
        self.assertEqual(variants[0]['estimated_seconds'], 7)
        self.assertNotIn('lift-tray', [s['method_id'] for s in variants[0]['path']])

    def test_repeats_needed_work_after_another_operation_undoes_a_result(self):
        doc = document({'a': 'raw', 'b': 'raw'}, {k: {k: 'ready'} for k in ['a', 'b']}, [
            op('finish-a', {'a': 'raw'}, {'a': 'ready'}),
            op('finish-b', {'a': 'ready', 'b': 'raw'}, {'a': 'raw', 'b': 'ready'})])
        variants, _ = search_actions(doc)
        self.assertEqual([s['method_id'] for s in variants[0]['path']], ['finish-a', 'finish-b', 'finish-a'])
        self.assertEqual(len({n['id'] for n in variants[0]['operations']}), 3)
        self.assertTrue(variants[0]['complete'])

    def test_places_productive_work_between_process_start_and_check(self):
        doc = document({'heater': 'off', 'a': 'raw', 'b': 'raw'},
            {k: {k: 'ready'} for k in ['a', 'b']}, [
                op('heat', {'heater': 'off'}, {'heater': 'on'}, starts={'heat': 20}),
                op('prepare-a', {'a': 'raw'}, {'a': 'ready'}, simulation_seconds=20),
                op('check-b', {'heater': 'on', 'b': 'raw'}, {'b': 'ready'}, awaits=['heat'])],
            wait_cost_per_sim_second=1)
        variants, _ = search_actions(doc)
        self.assertEqual([s['method_id'] for s in variants[0]['path']], ['heat', 'prepare-a', 'check-b'])
        self.assertEqual(variants[0]['estimated_seconds'], 3)


if __name__ == '__main__':
    unittest.main()
