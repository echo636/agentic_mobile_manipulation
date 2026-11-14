"""Finite contract, causal effects, and exact reference search checks (CPU only)."""
from collections import deque
from dataclasses import replace
import importlib.util
from pathlib import Path

import pytest

from manipulation_agent.tasks.order_fulfillment import (
    ABSENT, HELD, IN_BOX, Contract, assert_state, complete, initial,
    lower_bound, replay, transitions, used,
)

spec = importlib.util.spec_from_file_location(
    'verify_orders', Path(__file__).parents[1] / 'scripts/verify_order_fulfillment.py')
search = importlib.util.module_from_spec(spec)
spec.loader.exec_module(search)


def apply(task, actions):
    state = initial(task)
    for action in actions:
        state = {a: s for a, s, _ in transitions(state, task)}[action]
        assert_state(state, task)
    return state


def test_cut_preserves_lineage_capacity_and_carried_contents():
    task = Contract('test', (0, 1, 2), box_start=(0, 2))
    state = apply(task, ['pick:raw_a', 'put:raw_a:box_a', 'pick:raw_b',
                         'put:raw_b:box_a', 'pick:tool', 'cut:raw_a'])
    assert state[4] == ABSENT and state[5] != ABSENT
    assert state[6:8] == (IN_BOX[0], IN_BOX[0])
    assert used(state, 0) == 4
    state = apply(task, ['pick:raw_a', 'put:raw_a:box_a', 'pick:raw_b',
                         'put:raw_b:box_a', 'pick:tool', 'cut:raw_a',
                         'put:tool:box_a', 'pick:box_a', 'move:1'])
    assert state[0] == 1 and state[1] == HELD and used(state, 0) == 5
    assert state[3] == IN_BOX[0] and state[5] == IN_BOX[0]


def test_hand_and_capacity_constraints():
    task = Contract('test', (0, 1, 2), box_start=(0, 2), capacity=4)
    state = apply(task, ['pick:raw_a'])
    assert 'pick:raw_b' not in {a for a, _, _ in transitions(state, task)}
    state = apply(task, ['pick:raw_a', 'put:raw_a:box_a', 'pick:raw_b',
                         'put:raw_b:box_a', 'pick:tool'])
    assert 'put:tool:box_a' not in {a for a, _, _ in transitions(state, task)}
    assert 'cut:raw_a' in {a for a, _, _ in transitions(state, task)}


def test_all_terminal_relations_are_simultaneous():
    task = Contract('test', (0, 1, 2))
    goal = (0, 1, 2, 0, ABSENT, ABSENT, 3, 3, 4, 4)
    assert complete(goal, task) and lower_bound(goal, task) == 0
    for index, wrong in ((1, 0), (2, 0), (3, HELD), (3, 1), (6, 4), (8, 3)):
        state = list(goal); state[index] = wrong
        assert not complete(tuple(state), task)


def test_no_implicit_motion_or_processing():
    task = Contract('test', (0, 1, 2), tool_start=2)
    assert not replay(task, ['pick:tool'])['valid']
    assert not replay(task, ['cut:raw_a'])['valid']
    assert not replay(task, [])['complete']


@pytest.mark.parametrize('coordinates,tool_goal', [((0, 1, 2), 0), ((0, 1, 8), 0), ((0, 1, 8), 2)])
def test_a_star_agrees_with_uninformed_uniform_cost(coordinates, tool_goal):
    task = Contract('test', coordinates, tool_goal=tool_goal)
    fast = search.solve(task, timeout=20)
    reference = search.solve(task, timeout=20, heuristic=False)
    assert fast['status'] == reference['status'] == 'optimal_in_declared_model'
    assert fast['cost'] == reference['cost']
    assert replay(task, fast['actions'])['complete']


def test_heuristic_and_transitions_on_reachable_states():
    task = Contract('test', (0, 1, 3))
    queue = deque([initial(task)])
    seen = set(queue)
    checked = 0
    while queue and checked < 5000:
        state = queue.popleft()
        assert_state(state, task)
        h = lower_bound(state, task)
        for _, after, cost in transitions(state, task):
            assert_state(after, task)
            assert h <= cost + lower_bound(after, task)
            if after not in seen:
                seen.add(after); queue.append(after)
        checked += 1
    assert checked == 5000


def test_budget_exhaustion_does_not_claim_optimality():
    result = search.solve(Contract('test', (0, 1, 2)), max_expanded=0)
    assert result['status'] == 'budget_exhausted' and 'cost' not in result


def test_reference_site_constraints_do_not_change_public_capabilities():
    task = Contract('test', (0, 1, 2))
    actions_before = tuple(transitions(initial(task), task))
    source = search.solve(task, (0, 0))
    free = search.solve(task)
    assert source['cost'] > free['cost']
    assert tuple(transitions(initial(task), task)) == actions_before


def test_reject_underspecified_or_artificially_small_box():
    with pytest.raises(ValueError):
        Contract('bad', (0, 0, 1))
    with pytest.raises(ValueError):
        Contract('bad', (0, 1, 2), capacity=2)


def test_candidate_methods_use_public_actions_and_retain_better_proposal():
    from manipulation_agent.references.order_fulfillment_methods import compile_methods
    task = Contract('method_gap', (0, 3, 9), tool_goal=2)
    optimum = search.solve(task)
    result = compile_methods(task, optimum['actions'])
    assert result['selected']['name'] == 'supplied_proposal'
    assert result['selected']['cost'] == optimum['cost']
    assert result['oracle_used'] is False and result['optimality_proven'] is False
    for candidate in result['candidates']:
        if candidate['complete']:
            checked = replay(task, candidate['actions'])
            assert checked['complete'] and checked['cost'] == candidate['cost']


def test_mixed_method_can_be_better_than_uniform_processing_sites():
    from manipulation_agent.references.order_fulfillment_methods import compile_methods
    task = Contract('mixed', (0, -6, 1), tool_start=2)
    result = compile_methods(task)
    assert result['selected']['name'].startswith('mixed_')
    assert result['selected']['cost'] == search.solve(task)['cost']
    assert result['selected']['cost'] < search.solve(task, (0, 0))['cost']
    assert result['selected']['cost'] < search.solve(task, (1, 2))['cost']
