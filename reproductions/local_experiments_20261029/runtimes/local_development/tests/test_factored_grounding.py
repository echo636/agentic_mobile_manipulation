"""Exact best-binding scoring must not enumerate independent pair products."""
import random
import time
from types import SimpleNamespace

from manipulation_agent.goal_grounding import factored_ground_options, ground_options


def factors(count=3):
    return [SimpleNamespace(flattened_condition_options=[
        [['ontop', f'a{i}', 'b']], [['ontop', f'a{i}', 'c']]]) for i in range(count)]


def test_factored_sequence_and_best_score_match_exhaustive_real_predicates():
    compiled = factors()
    scope = {x: x for x in ['b', 'c', 'a0', 'a1', 'a2']}
    exhaustive = ground_options(compiled, scope, {})
    compact = factored_ground_options(compiled, scope, {}, threshold=1)
    assert [[p.body for p in o] for o in compact] == [[p.body for p in o] for o in exhaustive]
    rng = random.Random(7)
    for _ in range(32):
        truths = {(f'a{i}', d): rng.choice([True, False]) for i in range(3) for d in ['b', 'c']}
        evaluate = lambda cls, a, b: truths[(a, b)]
        expected = max(sum(bool(p.evaluate(evaluate)) for p in o) for o in exhaustive)
        assert sum(compact.best_values(evaluate)) == expected
    assert len(compact.unique_options()) == 6


def test_contradictions_and_variable_length_options_keep_original_path():
    a = ['ontop', 'a', 'b']
    compiled = [SimpleNamespace(flattened_condition_options=[[a], [['not', a]]]),
                SimpleNamespace(flattened_condition_options=[[a]])]
    assert isinstance(factored_ground_options(compiled, {'a': 'a', 'b': 'b'}, {}, threshold=1), list)
    compiled[0].flattened_condition_options = [[a], [a, a]]
    assert isinstance(factored_ground_options(compiled, {'a': 'a', 'b': 'b'}, {}, threshold=1), list)


def test_actual_dinner_product_is_bounded_and_truth_is_not_cached():
    from bddl.activity import Conditions, get_object_scope, get_goal_conditions
    c = Conditions('laying_restaurant_table_for_dinner', 0, 'behavior-1k')
    scope = get_object_scope(c)
    compiled = get_goal_conditions(c, scope)
    start = time.monotonic()
    compact = factored_ground_options(compiled, scope, c.parsed_objects)
    assert len(compact) == 24 ** 5
    assert len(compact.best_values(lambda *args: False)) == 28
    assert not any(compact.best_values(lambda *args: False))
    assert all(compact.best_values(lambda *args: True))
    from manipulation_agent.omnigibson_backend import OmniGibsonBackend
    backend = SimpleNamespace(research_instance={'task': c.behavior_activity},
                              env=SimpleNamespace(task=SimpleNamespace(
                                  ground_goal_state_options=compact,
                                  _evaluate_predicate=lambda *args: False)))
    assert OmniGibsonBackend._goal_options(backend) == [[False] * 28]
    assert time.monotonic() - start < 2
