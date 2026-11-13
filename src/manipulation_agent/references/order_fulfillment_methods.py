"""Finite complete-method comparison using only the public order contract.

This is a development reference strategy, not an RGB policy or a global solver.
There are 22 declared candidates. Every intermediate action is replayed against
public preconditions. No oracle, target plan, or optimum is imported.
"""
from itertools import permutations, product

from manipulation_agent.tasks.order_fulfillment import (
    ABSENT, BOXES, HALVES, HELD, IN_BOX, NAMES, RAWS, TOOL,
    Contract, complete, initial, location, replay, transitions,
)


class Infeasible(ValueError):
    pass


class Plan:
    def __init__(self, task):
        self.task, self.state, self.actions = task, initial(task), []

    def emit(self, action):
        choice = next((s for a, s, _ in transitions(self.state, self.task) if a == action), None)
        if choice is None:
            raise Infeasible(action)
        self.state = choice
        self.actions.append(action)

    def go(self, target):
        if self.state[0] != target:
            self.emit(f'move:{target}')

    def pick(self, item):
        self.go(location(self.state, item))
        self.emit(f'pick:{NAMES[item]}')

    def put(self, item, target='surface'):
        self.emit(f'put:{NAMES[item]}:{target}')

    def transfer(self, item, station, box=None):
        self.pick(item)
        self.go(station)
        self.put(item, NAMES[box] if box is not None else 'surface')

    def restore_tool(self):
        if self.state[TOOL + 1] != HELD:
            self.pick(TOOL)
        self.go(self.task.tool_goal)
        self.put(TOOL)

    def cut(self, order):
        self.go(location(self.state, RAWS[order]))
        self.emit(f'cut:{NAMES[RAWS[order]]}')


def at_destinations(task, delivery_order, cutting_order):
    p = Plan(task)
    for order in delivery_order:
        p.transfer(RAWS[order], location(p.state, order), order)
        if p.state[order + 1] != task.box_goal[order]:
            p.transfer(order, task.box_goal[order])
    p.pick(TOOL)
    for order in cutting_order:
        p.cut(order)
    p.restore_tool()
    for box in BOXES:
        if p.state[box + 1] != task.box_goal[box]:
            p.transfer(box, task.box_goal[box])
    return p


def at_source_direct(task, delivery_order):
    p = Plan(task)
    p.pick(TOOL)
    for order in BOXES:
        p.cut(order)
    p.restore_tool()
    for order in delivery_order:
        for half in HALVES[order]:
            p.transfer(half, location(p.state, order), order)
    for box in BOXES:
        if p.state[box + 1] != task.box_goal[box]:
            p.transfer(box, task.box_goal[box])
    return p


def shared_carrier(task, carrier, productive_fetch, co_ship_tool):
    """Return-trip preload, joint processing, then separate the two orders."""
    p = Plan(task)
    # This method has a declared common source; unsupported inputs are rejected.
    if task.raw_start[0] != task.raw_start[1]:
        raise Infeasible('This method requires a common material source')
    source, other = task.raw_start[0], 1 - carrier
    if productive_fetch:
        p.transfer(RAWS[carrier], location(p.state, carrier), carrier)
    if p.state[carrier + 1] != source:
        p.transfer(carrier, source)
    for order in BOXES:
        if p.state[RAWS[order] + 1] != IN_BOX[carrier]:
            p.transfer(RAWS[order], source, carrier)
    p.pick(TOOL)
    for order in BOXES:
        p.cut(order)
    if co_ship_tool:
        p.put(TOOL, NAMES[carrier])
    else:
        p.restore_tool()
    # The other order is unloaded at its current box; that box may then move.
    p.transfer(carrier, location(p.state, other))
    if co_ship_tool and p.state[0] == task.tool_goal:
        p.pick(TOOL); p.put(TOOL)
    for half in HALVES[other]:
        p.transfer(half, p.state[0], other)
    if p.state[other + 1] != task.box_goal[other]:
        p.transfer(other, task.box_goal[other])
    if p.state[carrier + 1] != task.box_goal[carrier]:
        p.transfer(carrier, task.box_goal[carrier])
    if co_ship_tool and p.state[TOOL + 1] != task.tool_goal:
        p.restore_tool()
    return p


def mixed_processing(task, destination_order, cutting_order, early_tool_return):
    """Preload one order; batch tool use across different processing stations."""
    p = Plan(task)
    source_order = 1 - destination_order
    p.transfer(RAWS[destination_order], location(p.state, destination_order), destination_order)
    if p.state[destination_order + 1] != task.box_goal[destination_order]:
        p.transfer(destination_order, task.box_goal[destination_order])
    p.pick(TOOL)
    for order in cutting_order:
        p.cut(order)
    if early_tool_return:
        p.restore_tool()
    else:
        p.put(TOOL)
    for half in HALVES[source_order]:
        p.transfer(half, location(p.state, source_order), source_order)
    for box in BOXES:
        if p.state[box + 1] != task.box_goal[box]:
            p.transfer(box, task.box_goal[box])
    if p.state[TOOL + 1] != task.tool_goal:
        p.restore_tool()
    return p


def compile_methods(task: Contract, proposal=None):
    candidates = []

    def add(name, build):
        try:
            p = build()
            result = replay(task, p.actions)
            candidates.append(dict(name=name, actions=p.actions, **{k: result[k] for k in
                ('valid', 'complete', 'cost', 'manipulations', 'travel', 'cut_sites')}))
        except Infeasible as exc:
            candidates.append(dict(name=name, valid=False, complete=False, reason=str(exc)))

    for delivery, cutting in product(permutations(BOXES), repeat=2):
        add(f'destination_delivery{delivery}_cut{cutting}',
            lambda d=delivery, c=cutting: at_destinations(task, d, c))
    for delivery in permutations(BOXES):
        add(f'source_direct{delivery}', lambda d=delivery: at_source_direct(task, d))
    for carrier, productive, co_ship in product(BOXES, (False, True), (False, True)):
        add(f'shared_box{carrier}_productive{productive}_tool{co_ship}',
            lambda b=carrier, p=productive, c=co_ship: shared_carrier(task, b, p, c))
    for destination_order, cutting, early in product(BOXES, tuple(permutations(BOXES)), (False, True)):
        add(f'mixed_destination{destination_order}_cut{cutting}_early_return{early}',
            lambda d=destination_order, c=cutting, e=early: mixed_processing(task, d, c, e))
    if proposal is not None:
        checked = replay(task, proposal)
        if checked['valid'] and checked['complete']:
            candidates.insert(0, dict(name='supplied_proposal', actions=proposal,
                                     **{k: checked[k] for k in ('valid', 'complete', 'cost', 'manipulations', 'travel', 'cut_sites')}))
    feasible = [c for c in candidates if c['valid'] and c['complete']]
    selected = min(feasible, key=lambda c: c['cost']) if feasible else None
    return dict(status='complete_candidate' if selected else 'no_complete_candidate',
                selected=selected, candidates=candidates, oracle_used=False,
                optimality_proven=False,
                scope='22 fixed whole-task methods; not complete over legal plans')
