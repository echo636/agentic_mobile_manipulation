import copy
import json
import unittest

from manipulation_agent.agent.task_planning import schedule_task, task_advice


def goal(key, methods=(), depends=(), parent='root', status='pending'):
    return dict(id=key, parent_id=parent, status=status, depends_on=list(depends), methods=list(methods))


def method(key, seconds, **kwargs):
    return dict(id=key, route=['bench'], seconds=seconds, **kwargs)


def notes(*goals, **context):
    return dict(format='goal-tree-v2', root_id='root',
                goals=[goal('root', parent=None), *goals],
                planning=dict(location='bench', state={}, **context))


class TaskPlanningTests(unittest.TestCase):
    def test_general_processing_order_reuses_tool_setup_across_branches(self):
        doc = notes(goal('a', [method('cut-a', 3, setup={'tool': 'knife'})]),
                    goal('b', [method('cut-b', 3, setup={'tool': 'knife'})]),
                    goal('c', [method('wipe-a', 3, setup={'tool': 'cloth'})], depends=['a']),
                    goal('d', [method('wipe-b', 3, setup={'tool': 'cloth'})], depends=['b']))
        doc['planning'].update(state={'tool': 'empty'}, switches=[
            dict(key='tool', **{'from': a, 'to': b}, at='bench', seconds=10)
            for a, b in [('empty', 'knife'), ('knife', 'cloth'), ('cloth', 'knife')]])
        plan = schedule_task(doc)
        self.assertEqual(plan['status'], 'estimated')
        self.assertEqual(plan['estimated_seconds'], 32)
        self.assertEqual({s['method_id'] for s in plan['steps'][:2]}, {'cut-a', 'cut-b'})
        self.assertEqual(sum(s['costs_seconds']['setup'] for s in plan['steps']), 20)

    def test_shared_inspection_and_alternative_assembly_methods_compare_whole_plan(self):
        doc = notes(goal('inspect-a', [method('inspect-a', 5), method('inspect-pair', 6, also=['inspect-b'])]),
                    goal('inspect-b', [method('inspect-b', 5)]),
                    goal('assemble', [method('assembly-by-hand', 18),
                                      method('assembly-with-jig', 7, requires={'jig': 'ready'})],
                         depends=['inspect-a', 'inspect-b']),
                    goal('jig', [method('prepare-jig', 3, effects={'jig': 'ready'})]),
                    goal('verify', [method('check-assembly', 2)], depends=['assemble']))
        original = copy.deepcopy(doc)
        plan = schedule_task(doc)
        self.assertEqual(plan['estimated_seconds'], 18)
        self.assertEqual({s['method_id'] for s in plan['steps']},
                         {'inspect-pair', 'prepare-jig', 'assembly-with-jig', 'check-assembly'})
        self.assertGreaterEqual(len(plan['alternatives']), 2)
        self.assertEqual(doc, original)

    def test_shared_transport_is_a_method_and_direct_can_be_cheaper(self):
        doc = notes(goal('a', [dict(method('direct-a', 2), route=['bench', 'table']),
                              dict(method('tray', 20, also=['b']), route=['bench', 'table'])]),
                    goal('b', [dict(method('direct-b', 2), route=['bench', 'table'])]),
                    travel=[dict(source='bench', destination='table', seconds=20),
                            dict(source='table', destination='bench', seconds=20)])
        self.assertEqual(schedule_task(doc)['next']['method_id'], 'tray')
        for edge in doc['planning']['travel']:
            edge['seconds'] = 1
        self.assertEqual(schedule_task(doc)['estimated_seconds'], 7)

    def test_selection_compares_the_current_graph_not_a_previous_next_step(self):
        doc = notes(goal('process', [method('manual', 10), method('powered', 3)]),
                    goal('verify', [method('check', 2)], depends=['process']))
        doc['active'] = dict(method_id='manual')
        selection = task_advice(json.dumps(doc))['selection']
        self.assertEqual(selection['recommended_method_id'], 'powered')
        self.assertEqual(selection['compared_remaining_seconds'], 12)
        self.assertEqual(selection['estimated_method_seconds'], 10)
        self.assertEqual(selection['estimated_method_costs_seconds'],
                         dict(work=10, travel=0, setup=0, recovery=0))
        self.assertEqual(selection['extra_estimated_seconds'], 7)
        doc['goals'][1]['status'] = 'done'
        doc['active'] = dict(method_id='check')
        selection = task_advice(json.dumps(doc))['selection']
        self.assertTrue(selection['matches_recommendation'])
        self.assertEqual(selection['extra_estimated_seconds'], 0)

    def test_partial_plans_only_compare_costs_for_the_same_results(self):
        doc = notes(goal('a', [method('a-fast', 1, requires={'hand': 'empty'},
                                     effects={'hand': 'occupied'}),
                              method('a-slow', 2, requires={'hand': 'empty'},
                                     effects={'hand': 'occupied'})]),
                    goal('b', [method('b', 10, requires={'hand': 'empty'},
                                     effects={'hand': 'occupied'})]))
        doc['planning']['state'] = {'hand': 'empty'}
        doc['active'] = dict(method_id='b')
        plan = task_advice(json.dumps(doc))
        self.assertEqual(plan['status'], 'partial')
        self.assertEqual(plan['comparison_goals'], ['a'])
        self.assertEqual([p['first_method'] for p in plan['alternatives']], ['a-fast', 'a-slow'])
        self.assertIsNone(plan['selection']['extra_estimated_seconds'])
        self.assertIsNone(plan['selection']['estimated_method_seconds'])

    def test_hierarchy_cross_branch_prerequisites_and_parent_verification(self):
        doc = notes(goal('access', [method('open', 2)]),
                    goal('branch', depends=['access']),
                    goal('x', [method('process', 4)], parent='branch'),
                    goal('use', [method('combine', 3)], depends=['branch']))
        doc['goals'][0]['methods'] = [method('verify-all', 1)]
        plan = schedule_task(doc)
        self.assertEqual([s['method_id'] for s in plan['steps']], ['open', 'process', 'combine', 'verify-all'])
        doc['goals'][1]['depends_on'] = ['use']
        self.assertIn('cycle', task_advice(json.dumps(doc))['reason'])

    def test_missing_work_routes_and_resource_changes_remain_visible(self):
        doc = notes(goal('local', [method('inspect', 2)]), goal('unknown'),
                    goal('remote', [dict(method('remote', 4), route=['shelf'])]),
                    goal('tool', [method('tool', 3, setup={'tool': 'knife'})]))
        plan = schedule_task(doc)
        self.assertEqual(plan['status'], 'partial')
        self.assertEqual(plan['coverage']['scheduled'], 1)
        self.assertEqual(plan['coverage']['unfinished'], 4)
        self.assertEqual(plan['coverage']['missing_methods'], ['unknown'])
        self.assertIn(['bench', 'shelf'], plan['gaps']['routes'])
        self.assertIn(['tool', None, 'knife', 'bench'], plan['gaps']['setup_transitions'])
        # A known multi-leg route can be combined without inventing an edge.
        doc['planning']['travel'] = [dict(source=a, destination=b, seconds=3)
                                    for a, b in [('bench', 'door'), ('door', 'shelf')]]
        plan = schedule_task(doc)
        self.assertEqual(plan['coverage']['scheduled'], 2)
        self.assertEqual(plan['steps'][-1]['route'], ['bench', 'door', 'shelf'])

    def test_reopening_failed_work_recomputes_and_completed_work_is_not_reprocessed(self):
        doc = notes(goal('a', [method('a', 2), method('both', 1, also=['b'])], status='done'),
                    goal('b', [method('b', 2)]),
                    goal('check', [method('check', 1)], depends=['a', 'b']))
        self.assertEqual([s['method_id'] for s in schedule_task(doc)['steps']], ['b', 'check'])
        doc['goals'][1]['status'] = 'pending'
        self.assertEqual([s['method_id'] for s in schedule_task(doc)['steps']], ['both', 'check'])
        doc['goals'][1]['status'] = 'blocked'
        plan = schedule_task(doc)
        self.assertEqual(plan['status'], 'partial')
        self.assertEqual([s['method_id'] for s in plan['steps']], ['b'])


if __name__ == '__main__':
    unittest.main()
