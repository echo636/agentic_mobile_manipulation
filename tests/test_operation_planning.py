"""Behavioral checks of operation-graph cost and execution integration."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from manipulation_agent.agent.operation_planning import operation_advice
from manipulation_agent.agent.task_notes import TaskNotes


def operation(key, at='kitchen', seconds=1, **fields):
    return dict(id=key, description=key, at=at, seconds=seconds, **fields)


def delivery(travel=100):
    original = [
        operation('pick-a', requires={'hand': 'empty'}, effects={'hand': 'a'}),
        operation('drop-a', 'guest', depends_on=['pick-a'], requires={'hand': 'a'},
                  effects={'hand': 'empty'}, achieves=['a']),
        operation('pick-b', requires={'hand': 'empty'}, effects={'hand': 'b'}),
        operation('drop-b', 'guest', depends_on=['pick-b'], requires={'hand': 'b'},
                  effects={'hand': 'empty'}, achieves=['b'])]
    batch = [
        operation('load-a', seconds=3, requires={'hand': 'empty'}),
        operation('load-b', seconds=3, requires={'hand': 'empty'}),
        operation('pick-carrier', seconds=2, depends_on=['load-a', 'load-b'],
                  requires={'hand': 'empty'}, effects={'hand': 'carrier'}),
        operation('stage-carrier', 'guest', seconds=2, depends_on=['pick-carrier'],
                  requires={'hand': 'carrier'}, effects={'hand': 'empty'}),
        operation('distribute-a', 'guest', seconds=3, depends_on=['stage-carrier'],
                  requires={'hand': 'empty'}, achieves=['a']),
        operation('distribute-b', 'guest', seconds=3, depends_on=['stage-carrier'],
                  requires={'hand': 'empty'}, achieves=['b'])]
    return dict(format='goal-tree-v2', root_id='root',
        goals=[dict(id='root', parent_id=None, status='active'),
               *[dict(id=k, parent_id='root', goal=k, status='pending') for k in ['a', 'b']]],
        planning=dict(location='kitchen', state={'hand': 'empty'}, travel=[
            dict(source=a, destination=b, seconds=travel)
            for a, b in [('kitchen', 'guest'), ('guest', 'kitchen')]]),
        execution=dict(operations=original, rewrites=[dict(id='carrier', description='Shared carrier trip',
            replaces=[op['id'] for op in original], operations=batch)]))


class OperationPlanningTests(unittest.TestCase):
    def test_more_handling_nodes_win_only_when_they_save_total_work(self):
        doc = delivery()
        before = copy.deepcopy(doc)
        advice = operation_advice(doc)
        self.assertEqual(advice['plan_id'], 'base/carrier')
        self.assertEqual(advice['estimated_seconds'], 116)
        self.assertEqual(advice['comparison']['baseline_estimated_seconds'], 304)
        self.assertEqual(len(advice['steps']), 6)
        self.assertEqual(doc, before)
        cheap_travel = operation_advice(delivery(1))
        self.assertEqual(cheap_travel['plan_id'], 'base')
        self.assertEqual(cheap_travel['estimated_seconds'], 7)
        started = delivery(1)
        started['execution']['operations'][0]['status'] = 'done'
        started['planning']['state']['hand'] = 'a'
        remaining = operation_advice(started)
        self.assertEqual(remaining['next']['operation_id'], 'drop-a')
        self.assertEqual(remaining['comparison']['graphs_considered'], 1)

    def test_shared_preparation_preserves_both_consumers_and_result_coverage(self):
        doc = delivery()
        doc['execution'] = dict(operations=[
            operation('setup-a', seconds=10, share='same-fixture', effects={'fixture': 'ready'}),
            operation('setup-b', seconds=10, share='same-fixture', effects={'fixture': 'ready'}),
            operation('process-a', depends_on=['setup-a'], requires={'fixture': 'ready'}, achieves=['a']),
            operation('process-b', depends_on=['setup-b'], requires={'fixture': 'ready'}, achieves=['b'])])
        advice = operation_advice(doc)
        self.assertEqual(advice['plan_id'], 'base/shared')
        self.assertEqual(advice['estimated_seconds'], 12)
        self.assertEqual(advice['coverage']['scheduled'], 2)
        graph = {op['id']: op for op in advice['operation_graph']}
        self.assertEqual(graph['process-a']['depends_on'], graph['process-b']['depends_on'])
        self.assertEqual(len(graph), 3)
        # Identical primitives on distinct items must remain distinct work.
        del doc['execution']['operations'][1]['share']
        self.assertEqual(operation_advice(doc)['estimated_seconds'], 22)

    def test_operation_order_overlaps_processing_and_preserves_rewrite_boundary(self):
        doc = delivery()
        doc['planning'].update(wait_cost_per_sim_second=1)
        doc['execution'] = dict(operations=[
            operation('start', starts={'heat': 20}),
            operation('prepare', simulation_seconds=20, achieves=['a']),
            operation('check', depends_on=['start'], awaits=['heat'], achieves=['b'])],
            rewrites=[dict(id='different-start', description='Alternate device startup', replaces=['start'],
                operations=[operation('ready-device', seconds=.2),
                    operation('start-device', seconds=.3, depends_on=['ready-device'], starts={'heat': 20})])])
        advice = operation_advice(doc)
        self.assertEqual([op['operation_id'] for op in advice['steps']],
                         ['ready-device', 'start-device', 'prepare', 'check'])
        self.assertEqual(advice['estimated_seconds'], 2.5)
        self.assertEqual(advice['operation_graph'][1]['depends_on'], ['start-device'])
        doc['execution']['rewrites'][0]['replaces'] = ['prepare']
        with self.assertRaisesRegex(ValueError, 'same declared task results'):
            operation_advice(doc)

    def test_selection_materializes_graph_and_feedback_does_not_complete_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            notes = TaskNotes(Path(tmp))
            metadata = dict(request_id='write', revision=0, agent_step=0)
            notes.write(json.dumps(delivery()), 0, **metadata)
            plan = notes.planning()
            op = plan['next']['operation_id']
            notes.select(json.dumps(dict(plan_id=plan['plan_id'], operation_id=op,
                steps=['Perform selected local operation'], next_check='Result visible')), 1, **metadata)
            saved = json.loads(notes.read()['content'])
            self.assertEqual(len(saved['execution']['operations']), 6)
            self.assertEqual(saved['execution']['rewrites'], [])
            context = notes.planning()['selected_operation']
            self.assertEqual(context['operation_id'], op)
            notes.record_action('act', {'primitive': 'place_on_top'},
                {'ok': True, 'runtime': {'tool_seconds': 4, 'observed_simulation_seconds': .1}}, 1)
            feedback = notes.planning()
            self.assertEqual(feedback['selected_operation'], context)
            self.assertIsNone(feedback['next'])
            self.assertTrue(all(g['status'] != 'done' for g in saved['goals']))
            notes.update(json.dumps({'execution': {'operations': [{'id': op, 'status': 'done'}]}}),
                         2, **metadata)
            self.assertEqual(len(json.loads(notes.read()['content'])['execution']['operations']), 6)
            self.assertEqual(notes.planning()['previous_execution']['operation_id'], op)
            self.assertNotEqual(notes.planning()['next']['operation_id'], op)

    def test_resource_gap_exposes_unscheduled_results_even_with_declared_producers(self):
        doc = delivery()
        doc['execution'] = dict(operations=[
            operation('prepare-a', requires={'hand': 'knife'}, achieves=['a']),
            operation('prepare-b', requires={'hand': 'empty'}, achieves=['b'])])
        advice = operation_advice(doc)
        self.assertEqual(advice['status'], 'partial')
        self.assertEqual(advice['coverage']['scheduled'], 1)
        self.assertEqual(advice['coverage']['unplanned'], ['a'])
        self.assertEqual(advice['coverage']['missing_operations'], [])
        self.assertEqual(advice['coverage']['operations_unplanned'], ['prepare-a'])
        self.assertEqual(advice['gaps']['required_states'], [['prepare-a', 'hand', 'knife']])
        self.assertEqual(advice['unplanned_results'], [{'id': 'a', 'goal': 'a'}])

    def test_harness_and_replay_keep_the_selected_replacement_graph(self):
        from manipulation_agent.contracts import Budget
        from manipulation_agent.observations.mock_rgb import MockRGBBackend
        from manipulation_agent.records import Recorder
        from manipulation_agent.runtime.vision_harness import VisionHarness
        from manipulation_agent.replay import build_replay
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'episode'
            recorder = Recorder(path, {'backend': 'mock', 'instruction': 'Integration fixture only'})
            harness = VisionHarness(MockRGBBackend(path), recorder,
                Budget(wall_seconds=None), require_task_notes=True)
            result = harness.call('task_notes', dict(operation='write',
                content=json.dumps(delivery()), expected_version=0), 'plan')
            plan = result['planning']
            selected = harness.call('task_notes', dict(operation='select', expected_version=1,
                content=json.dumps(dict(plan_id=plan['plan_id'], operation_id=plan['next']['operation_id'],
                    steps=['Perform the local operation'], next_check='Visible result'))), 'select')
            self.assertEqual(len(selected['selected_execution']['operations']), 6)
            action = harness.call('act', dict(primitive='toggle_on', revision=harness.revision,
                target=dict(image_ref=harness.snapshot['images'][0]['image_ref'], point=[.5, .5])), 'act')
            self.assertEqual(action['planning']['selected_operation']['operation_id'], plan['next']['operation_id'])
            replay = build_replay(path)
            recorded = next(s for s in replay['steps'] if s['request_id'] == 'act')['goal_state']
            self.assertEqual(recorded['execution'], json.loads(harness.task_notes.state['content'])['execution'])


if __name__ == '__main__':
    unittest.main()
