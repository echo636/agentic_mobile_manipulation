"""Schedule explicit material methods using resource last-use dependencies.

This searches orders of a small declared macro DAG, not arbitrary atomic actions.
The public transition model checks every emitted action. R3 candidates remain
available. More graph scheduling computation is explicitly added to that baseline.
"""
from dataclasses import asdict
from itertools import product

import compile_shared_resource_trips as previous

base = previous.previous.base


def graph(task, raw_right, raw_inside, extra, ship_tool, joint_return):
    nodes = []
    def add(name, operation, count=1, dependencies=(), resources=()):
        nodes.append(dict(name=name, operation=operation, count=count,
                          dependencies=list(dependencies), resources=list(resources)))
        return name

    left = task.count - raw_right - raw_inside
    right_nodes = [add(f'deliver_raw_{i}', 'deliver_raw',
                       dependencies=([f'deliver_raw_{i-1}'] if i else []))
                   for i in range(raw_right)]
    loads, cuts = [], []
    if raw_inside:
        loads.append(add('load_raw', 'load_raw', raw_inside, resources=['box']))
        cuts.append(add('cut_box', 'cut_box', raw_inside, ['load_raw'], ['tool', 'box']))
    if left:
        cuts.append(add('cut_left', 'cut_left', left, resources=['tool']))
    if right_nodes:
        cuts.append(add('cut_right', 'cut_right', raw_right, right_nodes, ['tool']))
    if extra:
        loads.append(add('load_products', 'load_products', extra, ['cut_left'], ['box']))
    payload = 2 * raw_inside + extra
    if payload:
        add('ship', 'ship_with_tool' if ship_tool else 'ship', dependencies=
            loads + (['cut_box'] if raw_inside else []),
            resources=['box'] + (['tool'] if ship_tool else []))
    for i in range(2 * left - extra):
        # Identical products need no permutations among themselves. The
        # carrier load and individual deliveries remain unordered.
        add(f'deliver_product_{i}', 'deliver_product', dependencies=
            ['cut_left'] + ([f'deliver_product_{i-1}'] if i else []))
    tool_uses = [n['name'] for n in nodes if 'tool' in n['resources']]
    box_uses = [n['name'] for n in nodes if 'box' in n['resources']]
    if joint_return:
        add('return_resources', 'return_resources', dependencies=sorted(set(tool_uses + box_uses)))
    else:
        add('return_tool', 'return_tool', dependencies=tool_uses)
        add('return_box', 'return_box', dependencies=box_uses)
    return nodes


def execute(plan, node):
    operation, count = node['operation'], node['count']
    if operation in ('deliver_raw', 'deliver_product'):
        plan.direct(operation.removeprefix('deliver_'), 0, 1, count)
    elif operation in ('load_raw', 'load_products'):
        plan.box_at(0)
        plan.release()
        plan.load('raw' if operation == 'load_raw' else 'product', count)
    elif operation.startswith('cut_'):
        plan.cut(1 if operation == 'cut_right' else 0, count, inside=operation == 'cut_box')
    elif operation in ('ship', 'ship_with_tool'):
        plan.release() if plan.state[7] != 3 else None
        plan.go(0)
        if operation == 'ship_with_tool':
            # Co-shipment is an explicit method, not an implicit free action.
            if plan.state[7] not in (0, 3):
                raise base.Unsupported('co-shipment requires a local tool')
            plan.get_tool()
            plan.emit('put_tool_box')
        else:
            plan.release()
        plan.ship(1)
    elif operation == 'return_resources':
        plan.finish(True)
    else:
        plan.return_one(operation.removeprefix('return_'))


def schedule(task, nodes):
    names = {node['name']: i for i, node in enumerate(nodes)}
    dependencies = [sum(1 << names[name] for name in node['dependencies']) for node in nodes]
    start = base.Plan(task)
    # Each layer completes exactly one node. State equality includes hand and
    # retained contents, so dominance does not erase future resource uses.
    layer = {(0, start.state): (0, (), ())}
    expanded = transitions = rejected = 0
    for _ in nodes:
        after = {}
        for (done, state), (cost, actions, order) in layer.items():
            expanded += 1
            for i, node in enumerate(nodes):
                if done & (1 << i) or dependencies[i] & done != dependencies[i]:
                    continue
                plan = base.Plan(task)
                plan.state, plan.cost, plan.actions = state, cost, list(actions)
                try:
                    execute(plan, node)
                except base.Unsupported:
                    rejected += 1
                    continue
                transitions += 1
                key = (done | (1 << i), plan.state)
                candidate = (plan.cost, tuple(plan.actions), (*order, node['name']))
                if key not in after or candidate[0] < after[key][0]:
                    after[key] = candidate
        layer = after
    completed = []
    for (_, state), (cost, actions, order) in layer.items():
        plan = base.Plan(task)
        plan.state, plan.cost, plan.actions = state, cost, list(actions)
        plan.release()
        if base.core.goal(plan.state, task):
            completed.append(dict(supported=True, success=True, cost=plan.cost,
                actions=plan.actions, final_state=plan.state, error=None, order=list(order)))
    selected = min(completed, key=lambda c: c['cost']) if completed else dict(
        supported=False, success=False, cost=None, actions=[], final_state=start.state,
        error='no legal complete schedule in this declared graph', order=[])
    return dict(selected, graph=nodes, scheduling=dict(expanded_labels=expanded,
        legal_macro_transitions=transitions, rejected_macro_transitions=rejected,
        complete_final_labels=len(completed)))


def compile_candidates(task):
    old = previous.compile_candidates(task)
    candidates = [dict(c, method=dict(family='r3', original=c['method'])) for c in old['candidates']]
    for right in range(task.count + 1):
        for inside in range(min(task.count - right, task.capacity // 2) + 1):
            maximum = min(2 * (task.count - right - inside), task.capacity - 2 * inside)
            for extra in range(maximum + 1):
                for ship_tool, joint_return in product((False, True), repeat=2):
                    if ship_tool and (not 2 * inside + extra or 2 * inside + extra + 1 > task.capacity):
                        continue
                    nodes = graph(task, right, inside, extra, ship_tool, joint_return)
                    result = schedule(task, nodes)
                    result['method'] = dict(family='dependency_graph', raw_right=right,
                        raw_inside=inside, extra_products=extra,
                        ship_tool=ship_tool, joint_return=joint_return)
                    candidates.append(result)
    successful = [i for i, c in enumerate(candidates) if c['success']]
    selected = min(successful, key=lambda i: candidates[i]['cost']) if successful else None
    graph_candidates = candidates[len(old['candidates']):]
    return dict(parameters=asdict(task), candidates=candidates, selected=selected,
        selected_cost=candidates[selected]['cost'] if selected is not None else None,
        baseline_cost=old['selected_cost'], graph_count=len(graph_candidates),
        graph_node_max=max(len(c['graph']) for c in graph_candidates),
        expanded_labels=sum(c['scheduling']['expanded_labels'] for c in graph_candidates),
        oracle_used=False, search_scope='topological schedules of declared macro DAGs plus frozen R3')
