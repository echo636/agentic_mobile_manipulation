import importlib.util
import random
from types import SimpleNamespace
import unittest

from manipulation_agent.goal_grounding import efficient_grounding, evaluate_once_per_literal, ground_options


@unittest.skipUnless(importlib.util.find_spec('bddl'), 'Requires pinned BDDL for differential checks')
class GroundingParity(unittest.TestCase):
    def test_literal_sequences_truth_and_partial_scores_match_upstream(self):
        from bddl.condition_evaluation import get_ground_state_options
        a=['ontop','a','b'];b=['inside','a','c'];c=['ontop','d','b']
        compiled=[SimpleNamespace(flattened_condition_options=[[a,a],[b],[['not',a]]]),
                  SimpleNamespace(flattened_condition_options=[[c],[a],[['not',b]]])]
        scope={x:x for x in 'abcd'}
        old=get_ground_state_options(compiled,scope=scope,object_map={})
        new=ground_options(compiled,scope=scope,object_map={})
        self.assertEqual([[n.body for n in o] for o in old],[[n.body for n in o] for o in new])
        rng=random.Random(0)
        for _ in range(32):
            truths={('ontop','a','b'):rng.choice([True,False]),('inside','a','c'):rng.choice([True,False]),('ontop','d','b'):rng.choice([True,False])}
            def evaluator(p,*args):return truths[(p.__name__.lower(),*args)]
            expected=[[n.evaluate(evaluator) for n in o] for o in old]
            with evaluate_once_per_literal(new):actual=[[n.evaluate(evaluator) for n in o] for o in new]
            self.assertEqual(actual,expected)
            self.assertEqual(max(sum(o)/len(o) for o in actual),max(sum(o)/len(o) for o in expected))
        self.assertLess(len({id(n) for o in new for n in o}),sum(map(len,new)))

    def test_no_truth_cache_across_scoring_passes_and_cleanup_on_error(self):
        compiled=[SimpleNamespace(flattened_condition_options=[[['ontop','a','b']],[['ontop','a','b']]])]
        options=ground_options(compiled,scope={'a':'a','b':'b'},object_map={})
        node=options[0][0]
        calls=[];truth=[True]
        def evaluator(*args):calls.append(args);return truth[0]
        with evaluate_once_per_literal(options):
            self.assertEqual([o[0].evaluate(evaluator) for o in options],[True,True])
        self.assertEqual(len(calls),1)
        truth[0]=False
        with self.assertRaises(RuntimeError),evaluate_once_per_literal(options):
            self.assertFalse(node.evaluate(evaluator));raise RuntimeError('probe')
        self.assertNotIn('evaluate',vars(node))
        self.assertFalse(node.evaluate(evaluator))

    def test_scoped_patch_restored_and_no_cross_task_nodes(self):
        import bddl.activity as activity
        original=activity.get_ground_state_options
        with efficient_grounding():self.assertIs(activity.get_ground_state_options,ground_options)
        self.assertIs(activity.get_ground_state_options,original)
        compiled=[SimpleNamespace(flattened_condition_options=[[['ontop','a','b']]])]
        first=ground_options(compiled,scope={'a':'a','b':'b'},object_map={})
        second=ground_options(compiled,scope={'a':'a','b':'b'},object_map={})
        self.assertIsNot(first[0][0],second[0][0])


if __name__=='__main__':unittest.main()
