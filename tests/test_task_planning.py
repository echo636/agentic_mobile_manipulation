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
    def test_alternatives_show_different_methods_after_a_shared_first_step(self):
        doc = notes(goal('inspect', [method('inspect', 1)]),
                    goal('a', [method('a', 1), method('shared', 4, also=['b', 'c'],
                         effects={'fixture': 'configured'})], depends=['inspect']),
                    goal('b', [method('b', 1)], depends=['inspect']),
                    goal('c', [method('c', 1)], depends=['inspect']))
        plan = schedule_task(doc)
        self.assertEqual(plan['estimated_seconds'], 4)
        self.assertEqual({tuple(sorted(p['method_order'])) for p in plan['alternatives']},
                         {('a', 'b', 'c', 'inspect'), ('inspect', 'shared')})
        self.assertEqual({p['first_method'] for p in plan['alternatives']}, {'inspect'})
        self.assertEqual([p['estimated_seconds'] for p in plan['alternatives']], [4, 5])

    def test_upfront_resource_investment_survives_cheap_prefixes_after_shared_inspection(self):
        items = [goal(str(i), [method('manual-' + str(i), 20),
                    method('fixture-' + str(i), 1, depends_on=['fixture'],
                           requires={'fixture': 'ready'})], depends=['inspect']) for i in range(7)]
        doc = notes(goal('inspect', [method('inspect', 1)]),
                    dict(goal('fixture', [method('prepare-fixture', 100,
                        effects={'fixture': 'ready'})], depends=['inspect']), optional=True), *items)
        doc['goals'][0]['methods'] = [method('verify-all', 2)]
        plan = schedule_task(doc)
        self.assertEqual(plan['estimated_seconds'], 110)
        self.assertEqual([s['method_id'] for s in plan['steps'][:2]], ['inspect', 'prepare-fixture'])
        self.assertEqual(plan['steps'][-1]['method_id'], 'verify-all')
        self.assertGreater(plan['pruned_states'], 0)  # Same bounded search, not exhaustive enumeration.
        # The investment must still lose when too little work remains.
        for item in doc['goals'][3:8]:
            item['status'] = 'done'
        plan = schedule_task(doc)
        self.assertEqual(plan['estimated_seconds'], 43)
        self.assertEqual(plan['optional_work']['scheduled'], [])

    def test_background_processing_overlaps_independent_work_and_keeps_result_check(self):
        doc = notes(goal('start', [method('start-device', 2, starts={'process': 60})]),
                    goal('clean', [method('clean', 8, simulation_seconds=45)]),
                    goal('assemble', [method('assemble', 4, simulation_seconds=20)], depends=['clean']),
                    goal('processed', [method('check-process', 2, awaits=['process'])], depends=['start']))
        doc['planning']['wait_cost_per_sim_second'] = .5
        doc['goals'][0]['methods'] = [method('verify-all', 1)]
        original = copy.deepcopy(doc)
        plan = schedule_task(doc)
        self.assertEqual([s['method_id'] for s in plan['steps']],
                         ['start-device', 'clean', 'assemble', 'check-process', 'verify-all'])
        self.assertEqual(plan['estimated_seconds'], 17)
        self.assertEqual(plan['steps'][3]['costs_seconds']['wait'], 0)
        self.assertGreater(next(p['estimated_seconds'] for p in plan['alternatives']
                                if p['first_method'] == 'clean'), 17)
        self.assertEqual(doc, original)  # Predicted time never marks real results done.

    def test_process_clocks_do_not_advance_with_tool_wall_time_or_unknown_progress(self):
        doc = notes(goal('work', [method('work', 1000)]),
                    goal('check', [method('check', 2, awaits=['device', 'drying'])], depends=['work']),
                    processes={'device': 20, 'drying': 30}, wait_cost_per_sim_second=2)
        plan = schedule_task(doc)
        self.assertEqual(plan['estimated_seconds'], 1062)
        self.assertEqual(plan['steps'][1]['process_timing']['wait_simulation_seconds'], 30)
        doc['goals'][1]['methods'][0]['simulation_seconds'] = 25
        plan = schedule_task(doc)
        self.assertEqual(plan['estimated_seconds'], 1012)
        self.assertEqual(plan['steps'][1]['process_timing']['wait_simulation_seconds'], 5)
        del doc['planning']['wait_cost_per_sim_second']
        plan = schedule_task(doc)
        self.assertEqual(plan['status'], 'estimated')
        self.assertEqual(plan['estimated_seconds'], 1007)
        self.assertEqual(plan['cost_assumptions'], {'wait_cost_per_sim_second': 1.})
        del doc['planning']['processes']['drying']
        self.assertIn(['check', 'drying'], schedule_task(doc)['gaps']['processes'])

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
                                      method('assembly-with-jig', 7, requires={'jig': 'ready'},
                                             depends_on=['jig'])],
                         depends=['inspect-a', 'inspect-b']),
                    dict(goal('jig', [method('prepare-jig', 3, effects={'jig': 'ready'})]), optional=True),
                    goal('verify', [method('check-assembly', 2)], depends=['assemble']))
        original = copy.deepcopy(doc)
        plan = schedule_task(doc)
        self.assertEqual(plan['estimated_seconds'], 18)
        self.assertEqual({s['method_id'] for s in plan['steps']},
                         {'inspect-pair', 'prepare-jig', 'assembly-with-jig', 'check-assembly'})
        self.assertGreaterEqual(len(plan['alternatives']), 2)
        self.assertGreater(plan['comparison']['distinct_method_sets'], 1)
        self.assertEqual(plan['comparison']['estimated_cost_range_seconds'][0], 18)
        self.assertEqual(doc, original)
        # A different task scale makes manual assembly cheaper. Do not force
        # the now-useless setup, or compare plans with different final results.
        doc['goals'][3]['methods'][0]['seconds'] = 4
        plan = schedule_task(doc)
        self.assertEqual(plan['estimated_seconds'], 12)
        self.assertEqual([s['method_id'] for s in plan['steps']],
                         ['inspect-pair', 'assembly-by-hand', 'check-assembly'])
        self.assertEqual(plan['optional_work']['scheduled'], [])
        self.assertNotIn('jig', plan['comparison_goals'])

    def test_optional_subtree_is_only_required_by_the_method_that_uses_it(self):
        doc = notes(dict(goal('fixture'), optional=True),
                    goal('configured', [method('configure', 2)], parent='fixture'),
                    goal('assembled', [method('manual', 9),
                        method('using-fixture', 3, depends_on=['fixture'])]))
        doc['goals'][0]['methods'] = [method('check-final', 1)]
        plan = schedule_task(doc)
        self.assertEqual(plan['estimated_seconds'], 6)
        self.assertEqual([s['method_id'] for s in plan['steps']],
                         ['configure', 'using-fixture', 'check-final'])
        self.assertEqual(plan['coverage']['scheduled'], 2)
        doc['goals'][1]['status'] = 'blocked'
        plan = schedule_task(doc)
        self.assertEqual(plan['status'], 'estimated')
        self.assertEqual([s['method_id'] for s in plan['steps']], ['manual', 'check-final'])
        self.assertEqual(plan['estimated_seconds'], 10)

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
        self.assertEqual(plan['selection']['estimated_method_seconds'], 10)
        self.assertTrue(plan['selection']['ready_in_authored_state'])
        self.assertEqual(plan['comparison']['estimated_cost_range_seconds'], [1, 2])
        self.assertEqual(plan['comparison']['distinct_method_sets'], 2)

    def test_selected_method_keeps_cost_outside_displayed_and_pruned_alternatives(self):
        doc = notes(goal('process', [method('process-' + str(i), i + 1) for i in range(5)]),
                    goal('verify', [method('check', 2)], depends=['process']))
        doc['active'] = dict(method_id='process-4')
        plan = schedule_task(doc)
        self.assertEqual(len(plan['alternatives']), 3)
        self.assertEqual(plan['selection']['estimated_method_seconds'], 5)
        self.assertEqual(plan['selection']['compared_remaining_seconds'], 7)
        self.assertEqual(plan['selection']['extra_estimated_seconds'], 4)
        pruned = schedule_task(doc, beam_width=1)
        self.assertEqual(pruned['selection']['estimated_method_seconds'], 5)
        self.assertIsNone(pruned['selection']['compared_remaining_seconds'])
        self.assertIsNone(pruned['selection']['extra_estimated_seconds'])
        doc['active'] = dict(method_id='check')
        self.assertFalse(schedule_task(doc)['selection']['ready_in_authored_state'])

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
        unknown = next(row for row in plan['unplanned_results'] if row['id'] == 'unknown')
        self.assertTrue(unknown['missing_method'])
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
