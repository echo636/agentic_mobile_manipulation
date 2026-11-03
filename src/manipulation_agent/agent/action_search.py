"""Compose operation DAGs from public, model-grounded state transitions.

Operators describe available local actions, not complete alternative plans.
Search can omit or repeat them. No simulator state or action execution occurs.
"""
import copy
from functools import cache
import hashlib
import heapq
import itertools
import json
import math

from .task_planning import WorkCosts, bindings, compile_goals, process_times


def compile_actions(notes):
    rows = notes['execution']['operators']
    operators = {op['id']: op for op in rows}
    if len(operators) != len(rows):
        raise ValueError('Operator IDs must be unique')
    work = [dict(id='__actions__', parent_id=None, status='pending')]
    for op in rows:
        if op.get('depends_on'):
            raise ValueError('Reusable operators use state preconditions, not occurrence dependencies')
        work.append(dict(id=op['id'], parent_id='__actions__', status='pending', methods=[
            {**op, 'route': [op['at']]}]))
    _, _, methods, _ = compile_goals(work)
    goals, _, _, optional = compile_goals(notes['goals'])
    parents = {g.get('parent_id') for g in goals.values()}
    required = {k: g for k, g in goals.items()
                if k not in optional and g.get('status') != 'retired' and
                (k not in parents or g.get('condition'))}
    conditions = {k: bindings(g['condition']) for k, g in required.items() if g.get('condition')}
    target = {}
    for condition in conditions.values():
        for key, value in condition.items():
            if key in target and target[key] != value:
                raise ValueError('Final goal conditions disagree on ' + key)
            target[key] = value
    return operators, methods, required, conditions, target


def relaxed_cost(state, methods, target):
    """Delete-relaxed h-max lower bound; additive estimate orders the search."""
    costs = {item: (0., 0.) for item in state.items()}
    changed = True
    while changed:
        changed = False
        for method in methods:
            pre = [costs.get(item, (math.inf, math.inf)) for item in method['requires'].items()]
            local = method['seconds'] + method['recovery_seconds']
            lower = max((c[0] for c in pre), default=0.) + local
            guide = sum(c[1] for c in pre) + local
            for item in {**method['setup'], **method['effects']}.items():
                old = costs.get(item, (math.inf, math.inf))
                new = min(old[0], lower), min(old[1], guide)
                if new != old:
                    costs[item] = new
                    changed = True
    needed = [costs.get(item, (math.inf, math.inf)) for item in target.items()]
    # Missing producers keep the complete-plan bound infinite, but must not
    # erase cost guidance for the reachable part of an incomplete task model.
    guide = sum(c[1] for c in needed if math.isfinite(c[1]))
    return max((c[0] for c in needed), default=0.), guide


def causal_graph(path, operators, conditions, plan_id):
    """Preserve producers and resource threats, leaving independent work branched."""
    nodes, writers, readers, ancestors = [], {}, {}, {}
    for i, step in enumerate(path):
        op = operators[step['method_id']]
        key = plan_id + ':' + str(i)
        reads = set(op.get('requires', {}))
        writes = set(op.get('effects', {})) | set(op.get('setup', {}))
        # Simulation clocks couple processing starts, productive work and checks.
        if op.get('starts') or op.get('awaits') or op.get('simulation_seconds'):
            reads.add('__process_clock__'); writes.add('__process_clock__')
        deps = {writers[k] for k in reads | writes if k in writers}
        deps.update(r for k in writes for r in readers.get(k, ()))
        # Transitive reduction removes redundant edges, not required actions.
        reduced = deps - set().union(*(ancestors[d] for d in deps))
        ancestors[key] = deps | set().union(*(ancestors[d] for d in deps))
        node = dict(id=key, operator_id=op['id'], description=op['description'],
                    at=op['at'], depends_on=sorted(reduced), achieves=[])
        nodes.append(node)
        for k in writes:
            writers[k] = key; readers[k] = set()
        for k in reads:
            readers.setdefault(k, set()).add(key)
    for goal, condition in conditions.items():
        supports = {writers[k] for k in condition if k in writers}
        if supports:
            tail = next(n for n in reversed(nodes) if n['id'] in supports)
            tail['achieves'].append(goal)
            tail['depends_on'] = sorted(set(tail['depends_on']) | (supports - {tail['id']}))
    return nodes


def search_actions(notes, max_states=16384):
    operators, methods, required, conditions, target = compile_actions(notes)
    context = notes['planning']; costs = WorkCosts(context, methods)
    @cache
    def estimate(values):
        # This relaxed bound depends on facts, not location or process clocks.
        return relaxed_cost(dict(values), methods, target)

    initial = (context['location'], tuple(sorted(bindings(context.get('state', {})).items())),
               tuple(sorted(process_times(context.get('processes', {})).items())))
    supplied = set(initial[1]) | {item for method in methods
                                for item in {**method['setup'], **method['effects']}.items()}
    missing_producers = [dict(goal_id=goal, condition={k: v for k, v in condition.items()
                                                    if (k, v) not in supplied})
                         for goal, condition in conditions.items() if not set(condition.items()) <= supplied]
    serial = itertools.count(); frontier = []; best_cost = {initial: 0.}
    lower, guide = estimate(initial[1])
    heapq.heappush(frontier, (guide, guide, next(serial), 0., lower, initial, []))
    finished, partial, expanded, incumbent = {}, (0, 0., [], initial), 0, math.inf
    applicable, rejected = set(), {}
    while frontier and expanded < max_states:
        _, _, _, total, bound, key, path = heapq.heappop(frontier)
        if total != best_cost.get(key) or total + bound > incumbent:
            continue
        at, values, clocks = key; state = dict(values)
        covered = {g for g, cond in conditions.items() if all(state.get(k) == v for k, v in cond.items())}
        if (len(covered), -total) > (partial[0], -partial[1]):
            partial = len(covered), total, path, key
        if all(state.get(k) == v for k, v in target.items()):
            signature = tuple(sorted(s['method_id'] for s in path))
            if signature not in finished or total < finished[signature][0]:
                finished[signature] = total, path, key
            incumbent = min(incumbent, total)
            continue
        expanded += 1
        for method in methods:
            costs.gaps = {name: set() for name in costs.gaps}
            transition = costs.apply(method, at, state, clocks)
            if transition is None:
                if method['id'] not in applicable:
                    reasons = rejected.setdefault(method['id'], {name: set() for name in costs.gaps})
                    for name, entries in costs.gaps.items():
                        reasons[name].update(entries)
                continue
            # A precondition rejected in one branch is not a model gap when
            # another explored state makes the same operator applicable.
            applicable.add(method['id'])
            rejected.pop(method['id'], None)
            step, after, after_clocks = transition
            successor = step['route'][-1], tuple(sorted(after.items())), tuple(sorted(after_clocks.items()))
            new_cost = total + step['estimated_seconds']
            if new_cost >= best_cost.get(successor, math.inf):
                continue
            bound, guide = estimate(successor[1])
            if new_cost + bound > incumbent:
                continue
            best_cost[successor] = new_cost
            # Equal-cost independent branches have many equivalent prefixes.
            # Prefer progress toward a complete plan before exploring siblings.
            heapq.heappush(frontier, (new_cost + guide, guide, next(serial), new_cost, bound,
                                      successor, [*path, step]))
    truncated = bool(frontier) and expanded >= max_states
    candidates = sorted(finished.values(), key=lambda item: item[0])[:3]
    if not candidates:
        candidates = [(partial[1], partial[2], partial[3])]
    variants = []
    for total, path, key in candidates:
        plan_id = 'search-' + hashlib.sha256(json.dumps(path, sort_keys=True).encode()).hexdigest()[:12]
        covered = {g for g, cond in conditions.items() if all(dict(key[1]).get(k) == v for k, v in cond.items())}
        graph = causal_graph(path, operators, {g: conditions[g] for g in covered}, plan_id)
        variants.append(dict(id=plan_id, operations=graph, path=path, estimated_seconds=total,
                             covered=covered, complete=set(required) <= covered))
    gaps = {name: set().union(*(reasons[name] for reasons in rejected.values())) for name in costs.gaps}
    return variants, dict(algorithm='bounded best-first state search with h-max cost bounds',
        expanded_states=expanded, search_truncated=truncated,
        optimal_in_declared_model=bool(finished) and not truncated and len(conditions) == len(required),
        missing_goal_conditions=sorted(set(required) - conditions.keys()),
        missing_producers=missing_producers,
        modeled_goal_ids=sorted(required),
        gap_scope='Operators never applicable in explored states; unused alternatives may be unnecessary',
        gaps={k: [list(x) if isinstance(x, tuple) else x for x in sorted(v, key=str)]
              for k, v in gaps.items()})


def action_advice(notes):
    variants, search = search_actions(notes); best = variants[0]
    pool = {op['id']: op for op in notes['execution']['operators']}
    selected_id = (notes.get('active') or {}).get('operation_id')
    selected = next((op for op in notes['execution'].get('operations', []) if op['id'] == selected_id), None)
    chosen = None; selected_step = None
    if selected:
        op = pool[selected['operator_id']]
        _, methods, _, _, _ = compile_actions(notes)
        transition = WorkCosts(notes['planning'], methods).apply(
            next(m for m in methods if m['id'] == op['id']), notes['planning']['location'],
            notes['planning'].get('state', {}), tuple(notes['planning'].get('processes', {}).items()))
        if transition:
            selected_step = transition[0]
            goals = {g['id']: g for g in notes['goals']}
            chosen = dict(operation_id=selected_id, description=op['description'], action=op.get('action'),
                route=selected_step['route'], requires=op.get('requires', {}), predicted_effects=op.get('effects', {}),
                check=op.get('check'), results=[dict(id=g, goal=goals[g].get('goal', g), check=goals[g].get('check'))
                                              for g in selected.get('achieves', [])])
    steps = [{**{k: v for k, v in step.items() if k != 'method_id'},
              'operation_id': node['id'], 'operator_id': node['operator_id'], 'goals': node['achieves'],
              'action': pool[node['operator_id']].get('action')}
             for node, step in zip(best['operations'], best['path'])]
    missing = sorted(set(search['modeled_goal_ids']) - best['covered'])
    return dict(status='estimated' if best['complete'] else 'partial',
        representation='operation-dag-v1', source='program_search_over_model_grounded_operators',
        scope='Predicted state transitions; public RGB and receipts must verify feasibility and results',
        active=notes.get('active'), selected_operation=chosen,
        selection=dict(operation_id=selected_id, recommended_operation_id=steps[0]['operation_id'] if steps else None,
            ready_in_authored_state=bool(selected_step) if selected else None,
            estimated_operation_seconds=selected_step['estimated_seconds'] if selected_step else None,
            estimated_operation_costs_seconds=selected_step['costs_seconds'] if selected_step else None),
        plan_id=best['id'], operation_graph=best['operations'], graph_changes=[dict(kind='automatic_state_search')],
        steps=steps, next=steps[0] if steps else None, estimated_seconds=best['estimated_seconds'],
        coverage=dict(scheduled=len(best['covered']), unfinished=len(search['modeled_goal_ids']),
                      unplanned=missing, operations_scheduled=len(steps)),
        unplanned_results=[dict(id=g, goal=next(n.get('goal', g) for n in notes['goals'] if n['id'] == g)) for g in missing],
        alternatives=[dict(plan_id=v['id'], estimated_seconds=v['estimated_seconds'],
            operation_order=[n['id'] for n in v['operations']], operator_order=[n['operator_id'] for n in v['operations']])
            for v in variants if v['complete']], comparison=search)


def select_action_graph(notes, choice):
    variants, _ = search_actions(notes)
    chosen = next(v for v in variants if v['id'] == choice['plan_id'])
    operation = next(op for op in chosen['operations'] if op['id'] == choice['operation_id'])
    notes['execution']['operations'] = copy.deepcopy(chosen['operations'])
    notes['execution']['last_changes'] = [dict(kind='automatic_state_search', plan_id=chosen['id'])]
    notes['active'] = dict(operation_id=operation['id'], serves=operation['achieves'],
                           steps=choice['steps'], next_check=choice['next_check'])
    return notes
