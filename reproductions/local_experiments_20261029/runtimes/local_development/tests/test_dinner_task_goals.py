"""Native compiler checks for the dinner design; no physical-state claim."""
import importlib.util
from pathlib import Path
import unittest


@unittest.skipUnless(importlib.util.find_spec('bddl'), 'Requires pinned BDDL')
class DinnerGoals(unittest.TestCase):
    def setUp(self):
        from bddl.activity import Conditions, get_goal_conditions, get_object_scope
        problem = Path(__file__).resolve().parents[1] / 'tasks/clear_dinner_and_store_leftovers/problem0.bddl'
        conditions = Conditions('clear_dinner_and_store_leftovers', 0, 'behavior-1k',
                                predefined_problem=problem.read_text())
        self.goals = get_goal_conditions(conditions, get_object_scope(conditions))

    def relations(self, boxes=(1, 2)):
        inside = 'inside'
        fridge = 'electric_refrigerator.n.01_1'
        sink = 'sink.n.01_1'
        return {
            (inside, 'chicken.n.01_1', f'tupperware.n.01_{boxes[0]}'),
            (inside, 'spinach.n.02_1', f'tupperware.n.01_{boxes[1]}'),
            *((inside, f'tupperware.n.01_{i}', fridge) for i in (1, 2)),
            *((inside, item, sink) for item in
              ('plate.n.04_1', 'mug.n.04_1', 'mug.n.04_2', 'teaspoon.n.02_1')),
            (inside, 'tray.n.01_1', 'cabinet.n.01_1'),
        }

    def satisfied(self, relations):
        return all(goal.evaluate(lambda pred, *args: (pred.__name__.lower(), *args) in relations)
                   for goal in self.goals)

    def test_both_complete_box_assignments_accepted(self):
        for boxes in ((1, 2), (2, 1)):
            with self.subTest(boxes=boxes):
                self.assertTrue(self.satisfied(self.relations(boxes)))

    def test_each_required_inside_relation_is_necessary(self):
        for boxes in ((1, 2), (2, 1)):
            relations = self.relations(boxes)
            for missing in sorted(relations):
                with self.subTest(boxes=boxes, missing=missing):
                    self.assertFalse(self.satisfied(relations - {missing}))

    def test_two_foods_in_one_box_cannot_mix_branches(self):
        for box in (1, 2):
            with self.subTest(box=box):
                self.assertFalse(self.satisfied(self.relations((box, box))))

    def test_final_open_door_rejects_otherwise_complete_state(self):
        for door in ('electric_refrigerator.n.01_1', 'cabinet.n.01_1'):
            with self.subTest(door=door):
                self.assertFalse(self.satisfied(self.relations() | {('open', door)}))

    def test_initially_closed_doors_do_not_complete_task(self):
        self.assertFalse(self.satisfied(set()))

    def test_wrong_tray_destination_is_rejected(self):
        relations = self.relations()
        relations.remove(('inside', 'tray.n.01_1', 'cabinet.n.01_1'))
        relations.add(('inside', 'tray.n.01_1', 'sink.n.01_1'))
        self.assertFalse(self.satisfied(relations))
