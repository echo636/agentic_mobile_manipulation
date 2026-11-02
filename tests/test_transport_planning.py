import unittest
import json

from manipulation_agent.agent.transport_planning import (
    Carrier, HandlingCosts, Preparation, Transfer, compare_transport_batches, ready_goals,
    schedule_transport, transport_advice)


class TransportPlanningTests(unittest.TestCase):
    def prepared_plan(self, distance):
        graph = {'d0': []}
        for i in range(1, 4):
            graph['p'+str(i)] = []
            graph['d'+str(i)] = ['p'+str(i)]
        return schedule_transport(graph, set(),
            [Transfer('d'+str(i), 'k', 't', compatibility='food') for i in range(4)],
            [Carrier('tray', 'k', 4, frozenset({'food'}))],
            [Preparation('p'+str(i), 'k', 3) for i in range(1, 4)],
            robot_location='k', travel_seconds={('k','t'):distance, ('t','k'):distance},
            handling=HandlingCosts(2, 2, 2, 4, 2))

    def test_finishing_preparation_before_departure_beats_delivering_ready_item(self):
        plan = self.prepared_plan(20)
        self.assertEqual(plan['status'], 'estimated')
        self.assertEqual([s['method'] for s in plan['steps']], ['prepare']*3+['carrier'])
        self.assertEqual(plan['estimated_seconds'], 49)
        self.assertEqual(set(plan['steps'][-1]['goals']), {'d0','d1','d2','d3'})
        self.assertEqual(plan['steps'][-1]['depends_on_goals'], ['p1','p2','p3'])
        near = self.prepared_plan(1)
        self.assertEqual(near['estimated_seconds'], 24)
        self.assertNotIn('carrier', [s['method'] for s in near['steps']])

    def test_shared_carrier_location_follows_first_delivery(self):
        plan = schedule_transport({'a': [], 'b':['a']}, set(),
            [Transfer('a','k','t',compatibility='food'), Transfer('b','t','u',compatibility='food')],
            [Carrier('tray','k',4,frozenset({'food'}))], [], robot_location='k',
            travel_seconds={('k','t'):20, ('t','k'):20, ('t','u'):20},
            handling=HandlingCosts(100,1,1,1,1))
        self.assertEqual(plan['estimated_seconds'], 46)
        self.assertEqual(plan['steps'][1]['route'], ['t','u'])
        self.assertEqual(plan['final_location'], 'u')

    def test_unmodelled_preparation_is_not_treated_as_completed(self):
        plan = schedule_transport({'p': [], 'd':['p']}, set(), [Transfer('d','k','t')], [], [],
            robot_location='k', travel_seconds={('k','t'):20}, handling=HandlingCosts(2,2,2,4,2))
        self.assertEqual(plan['status'], 'needs_revision')
        self.assertEqual(plan['missing_preparations'], ['p'])

    def compare(self, distance, capacity=4, carrier_location='kitchen', return_required=False):
        items = [Transfer(str(i), 'kitchen', 'table', compatibility='tableware') for i in range(4)]
        graph = {t.goal_id: [] for t in items}
        return compare_transport_batches(graph, set(), items,
            [Carrier('tray', carrier_location, capacity, frozenset({'tableware'}), return_required)],
            robot_location='kitchen', travel_seconds={('kitchen', 'table'): distance,
                ('table', 'kitchen'): distance}, handling=HandlingCosts(2, 2, 2, 4, 2))[0]

    def test_far_deliveries_share_trip_but_nearby_items_do_not(self):
        far = self.compare(20)
        self.assertEqual(far['recommended']['method'], 'carrier')
        self.assertEqual(far['recommended']['estimated_seconds'], 40)
        self.assertEqual(far['estimated_saving_vs_direct'], 108)
        self.assertEqual(self.compare(1)['recommended']['method'], 'direct')

    def test_capacity_collection_and_return_are_real_costs(self):
        split = self.compare(20, capacity=2)['recommended']
        self.assertEqual(split['batches'], [['0', '1'], ['2', '3']])
        self.assertEqual(split['estimated_seconds'], 84)
        self.assertEqual(self.compare(20, carrier_location='table')['recommended']['estimated_seconds'], 80)
        self.assertEqual(self.compare(20, return_required=True)['recommended']['estimated_seconds'], 82)

    def test_dependencies_and_incompatible_items_cannot_be_batched(self):
        graph = {'cut': [], 'raw': ['cut'], 'ready': [], 'clean': [], 'unknown': []}
        items = [Transfer('raw', 'k', 't', compatibility='food'),
                 Transfer('ready', 'k', 't', compatibility='food'),
                 Transfer('clean', 'k', 't', compatibility='cloth'), Transfer('unknown', 'k', 't')]
        result = compare_transport_batches(graph, set(), items,
            [Carrier('tray', 'k', 4, frozenset({'food'}))], robot_location='k',
            travel_seconds={('k', 't'): 20, ('t', 'k'): 20}, handling=HandlingCosts(2, 2, 2, 4, 2))
        self.assertEqual([r['goals'] for r in result], [['ready'], ['clean'], ['unknown']])
        self.assertEqual([len(r['alternatives']) for r in result], [2, 1, 1])
        self.assertIn('raw', ready_goals(graph, {'cut'}))

    def test_unknown_route_does_not_become_free_carrier_collection(self):
        options = self.compare(20, carrier_location='unexplored')['alternatives']
        self.assertEqual([x['method'] for x in options], ['direct'])
        with self.assertRaisesRegex(ValueError, 'cycle'):
            ready_goals({'a': ['b'], 'b': ['a']}, set())


    def test_notes_inherit_parent_prerequisites_and_reopen_after_spill(self):
        notes = dict(goals=[dict(id='access', parent_id=None, status='pending'),
            dict(id='deliveries', parent_id=None, status='active', depends_on=['access']),
            *[dict(id=str(i), parent_id='deliveries', status='pending') for i in range(4)]],
            transport=dict(robot_location='k',
                items=[dict(goal_id=str(i), source='k', destination='t', compatibility='dishes') for i in range(4)],
                carriers=[dict(label='tray', location='k', capacity=4, compatible_groups=['dishes'])],
                travel=[dict(source='k', destination='t', seconds=20), dict(source='t', destination='k', seconds=20)],
                handling_seconds=dict(direct_item=2, load_item=2, unload_item=2, carrier_trip=4, carrier_return=2)))
        self.assertEqual(transport_advice(json.dumps(notes))['groups'], [])
        notes['goals'][0]['status'] = 'done'
        self.assertEqual(transport_advice(json.dumps(notes))['groups'][0]['recommended']['method'], 'carrier')
        for goal in notes['goals'][2:]:
            goal['status'] = 'done'
        self.assertEqual(transport_advice(json.dumps(notes))['groups'], [])
        notes['goals'][2]['status'] = 'pending'
        self.assertEqual(transport_advice(json.dumps(notes))['groups'][0]['goals'], ['0'])


if __name__ == '__main__':
    unittest.main()
