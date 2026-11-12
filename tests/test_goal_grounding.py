import importlib.util
import random
from types import SimpleNamespace
import unittest

from manipulation_agent.tasks.goal_grounding import (
    FactoredGoalOptions, efficient_grounding, evaluate_once_per_literal,
    factored_ground_options, ground_options,
)


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

    def test_factored_score_matches_exhaustive_with_variable_lengths_and_duplicates(self):
        from bddl.condition_evaluation import get_ground_state_options
        a=['ontop','a','b'];b=['inside','a','c'];c=['ontop','d','b']
        groups=[SimpleNamespace(flattened_condition_options=[[a,a],[b,c],[c]]),
                SimpleNamespace(flattened_condition_options=[[a],[b,b,c]])]
        scope={x:x for x in 'abcd'}
        exhaustive=get_ground_state_options(groups,scope=scope,object_map={})
        factored=factored_ground_options(groups,scope=scope,object_map={})
        self.assertIsInstance(factored,FactoredGoalOptions)
        rng=random.Random(1)
        for _ in range(32):
            truths={('ontop','a','b'):rng.choice([True,False]),('inside','a','c'):rng.choice([True,False]),('ontop','d','b'):rng.choice([True,False])}
            def evaluator(p,*args):return truths[(p.__name__.lower(),*args)]
            expected=[[n.evaluate(evaluator) for n in o] for o in exhaustive]
            best=factored.best_truth_option(evaluator)
            self.assertEqual(sum(best)/len(best),max(sum(o)/len(o) for o in expected))
            self.assertEqual(all(best),any(all(o) for o in expected))
        conflicting=[*groups,SimpleNamespace(flattened_condition_options=[[['not',a]]])]
        self.assertIsInstance(factored_ground_options(conflicting,scope=scope,object_map={}),list)

    def test_official_preschool_compiles_without_cartesian_materialization(self):
        from bddl.activity import Conditions, get_goal_conditions, get_object_scope, get_ground_goal_state_options
        from bddl.condition_evaluation import evaluate_state
        conditions=Conditions('set_up_a_preschool_classroom',0,'behavior-1k')
        scope=get_object_scope(conditions)
        goals=get_goal_conditions(conditions,scope,generate_ground_options=False)
        with efficient_grounding(factorized=True):
            options=get_ground_goal_state_options(conditions,scope,goals)
        self.assertIsInstance(options,FactoredGoalOptions)
        self.assertEqual(len(options),18_662_400_000)
        self.assertLess(sum(map(len,options.groups)),1000)
        for truth in (False,True):
            evaluator=lambda *args:truth
            success,_=evaluate_state(goals,evaluator)
            self.assertEqual(all(options.best_truth_option(evaluator)),success)

    def test_nested_quantified_conjunctions_remain_factored(self):
        import bddl.activity as activity
        from bddl.condition_evaluation import compile_state, get_ground_state_options
        body=[['forall',['?x','-','thing'],['and',
            ['or',['ontop','?x','b'],['inside','?x','c']],
            ['or',['ontop','?x','c'],['inside','?x','b']]]]]
        scope={x:x for x in 'abcd'}; objects={'thing':['a','d']}
        expected=get_ground_state_options(compile_state(body,scope,objects),scope,objects)
        options=factored_ground_options(compile_state(body,scope,objects,generate_ground_options=False),scope,objects)
        self.assertEqual(len(options),len(expected))
        for seed in range(8):
            rng=random.Random(seed)
            truth={(p,x,y):rng.choice([False,True]) for p in ('ontop','inside') for x in 'ad' for y in 'bc'}
            evaluator=lambda p,*args:truth[(p.__name__.lower(),*args)]
            full=[[n.evaluate(evaluator) for n in o] for o in expected]
            best=options.best_truth_option(evaluator)
            self.assertEqual(sum(best)/len(best),max(sum(o)/len(o) for o in full))
        conds=activity.Conditions('make_nachos',0,'behavior-1k')
        with efficient_grounding(factorized=True):
            scope=activity.get_object_scope(conds)
            goals=activity.get_goal_conditions(conds,scope)
            nachos=activity.get_ground_goal_state_options(conds,scope,goals)
        self.assertEqual(len(nachos),2**36)
        self.assertLess(sum(map(len,nachos.groups)),200)


if __name__=='__main__':unittest.main()
