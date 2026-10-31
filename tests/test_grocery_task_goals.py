"""Check task semantics through the pinned BDDL compiler, without simulation."""
import importlib.util
from pathlib import Path
import unittest


@unittest.skipUnless(importlib.util.find_spec('bddl'), 'Requires pinned BDDL')
class GroceryGoals(unittest.TestCase):
    def setUp(self):
        from bddl.activity import Conditions, get_goal_conditions, get_object_scope
        problem = Path(__file__).resolve().parents[1] / 'tasks/carry_in_and_put_away_groceries/v2_design/problem0.bddl'
        conds = Conditions('carrying_in_groceries', 0, 'behavior-1k',
                           predefined_problem=problem.read_text())
        self.goals = get_goal_conditions(conds, get_object_scope(conds))

    def satisfied(self, cabinets=(1, 1), missing=(), open_cabinets=()):
        fridge = 'electric_refrigerator.n.01_1'
        relations = {('inside', item, fridge) for item in (
            'beefsteak_tomato.n.01_1', 'carton__of__milk.n.01_1',
            'carton__of__milk.n.01_2', 'swiss_cheese.n.01_1')}
        relations.update(('inside', f'butter_cookie.n.01_{i}', f'cabinet.n.01_{cabinet}')
                         for i, cabinet in enumerate(cabinets, 1))
        relations.update(('nextto', f'sack.n.01_{i}', fridge) for i in (1, 2))
        relations.update(('open', f'cabinet.n.01_{i}') for i in open_cabinets)
        relations = {r for r in relations if r[1] not in missing}
        return all(g.evaluate(lambda pred, *args: (pred.__name__.lower(), *args) in relations)
                   for g in self.goals)

    def test_either_kitchen_cabinet_and_split_storage_are_valid(self):
        for first in (1, 2):
            for second in (1, 2):
                with self.subTest(first=first, second=second):
                    self.assertTrue(self.satisfied((first, second)))

    def test_each_milk_and_cookie_is_required(self):
        for item in ('carton__of__milk.n.01_1', 'carton__of__milk.n.01_2',
                     'butter_cookie.n.01_1', 'butter_cookie.n.01_2'):
            with self.subTest(item=item):
                self.assertFalse(self.satisfied(missing=(item,)))

    def test_unlisted_container_is_not_a_destination(self):
        self.assertFalse(self.satisfied((3, 3)))

    def test_both_kitchen_cabinets_must_be_closed_at_finish(self):
        for cabinet in (1, 2):
            with self.subTest(cabinet=cabinet):
                self.assertFalse(self.satisfied(open_cabinets=(cabinet,)))


if __name__ == '__main__':
    unittest.main()
