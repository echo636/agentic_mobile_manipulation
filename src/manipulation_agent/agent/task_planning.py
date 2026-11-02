"""Costed work DAG over model-authored goals, methods and public beliefs.

No simulator access, action execution, or automatic claims of goal completion.
Methods are finite alternatives for achieving results, not primitive templates.
"""
import json
import math

from .transport_planning import ready_goals


def seconds(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError('Costs must be finite nonnegative seconds')
    return float(value)


def bindings(value):
    if not isinstance(value, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                          for k, v in value.items()):
        raise ValueError('Resource states use named string keys and values')
    return value


def process_times(value):
    if not isinstance(value, dict) or any(not isinstance(k, str) or not k for k in value):
        raise ValueError('Processes use names and remaining simulation seconds')
    return {k: seconds(v) for k, v in value.items()}


def compile_goals(rows):
    """Inherit branch prerequisites; expand structural parents to their work.

    A parent's own verification can be a method or a separate leaf. Structural
    closure here is a planning prediction only; authored statuses never change.
    """
    goals = {g['id']: g for g in rows}
    if not goals or len(goals) != len(rows):
        raise ValueError('Use unique goal IDs and at least one goal')
    children = {key: set() for key in goals}
    for key, goal in goals.items():
        parent = goal.get('parent_id')
        if parent is not None:
            children[parent].add(key)
    chains = {}
    for key in goals:
        chain, current = [], key
        while current is not None:
            if current in chain:
                raise ValueError('Goal parentage contains a cycle')
            chain.append(current)
            current = goals[current].get('parent_id')
        chains[key] = chain
    work = {key for key, g in goals.items() if not children[key] or g.get('methods')}

    def expand(key):
        if goals[key]['status'] == 'done':
            return set()
        if key in work:
            return {key}
        return set().union(*(expand(child) for child in children[key]
                             if not goals[child].get('optional', False)))

    pending = {key for key in work if goals[key]['status'] not in ('done', 'retired')}
    optional = {key for key in goals
                if any(goals[a].get('optional', False) for a in chains[key])}
    dependencies = {}
    for key in pending:
        deps = set().union(*(expand(dep) for ancestor in chains[key]
                            for dep in goals[ancestor].get('depends_on', [])))
        # An internal verification method runs after its contributing children.
        deps.update(set().union(*(expand(child) for child in children[key]
                                 if not goals[child].get('optional', False))))
        dependencies[key] = deps
    ready_goals(dependencies, set())  # Reject cycles and retired/missing prerequisites.
    methods = []
    for key in sorted(pending):
        if any(goals[a]['status'] == 'blocked' for a in chains[key]):
            continue
        for method in goals[key].get('methods', []):
            achieves = {key, *method.get('also', [])}
            if not achieves <= work:
                raise ValueError('Shared methods must name executable goal IDs')
            if not achieves <= pending or any(goals[a]['status'] == 'blocked'
                    for g in achieves for a in chains[g]):
                continue  # Do not redo already completed work in a shared batch.
            route = method['route']
            if not isinstance(route, list) or not route or any(not isinstance(p, str) or not p for p in route):
                raise ValueError('Every method needs a nonempty route of observed area labels')
            awaits = method.get('awaits', [])
            if not isinstance(awaits, list) or any(not isinstance(p, str) or not p for p in awaits):
                raise ValueError('awaits names processes to check after their estimated delay')
            methods.append(dict(id=method['id'], goals=sorted(achieves), route=route,
                depends_on=set().union(*(expand(dep) for dep in method.get('depends_on', []))),
                seconds=seconds(method['seconds']), recovery_seconds=seconds(method.get('recovery_seconds', 0)),
                requires=bindings(method.get('requires', {})), setup=bindings(method.get('setup', {})),
                effects=bindings(method.get('effects', {})),
                simulation_seconds=seconds(method.get('simulation_seconds', 0)),
                starts=process_times(method.get('starts', {})), awaits=awaits))
    if len({m['id'] for m in methods}) != len(methods):
        raise ValueError('Method IDs must be unique across the tree')
    return goals, dependencies, methods, optional


def schedule_task(notes, beam_width=32):
    """Compare methods and order across all unfinished modeled branches.

    Each method advances at least one goal, so search is finite. Equivalent
    states merge, retaining distinct first choices for useful comparisons.
    Unknown routes, resource states and methods remain explicit gaps.
    """
    goals, dependencies, methods, optional = compile_goals(notes['goals'])
    context = notes['planning']
    location = context['location']
    state = bindings(context.get('state', {}))
    processes = process_times(context.get('processes', {}))
    wait_rate = context.get('wait_cost_per_sim_second')
    if wait_rate is not None:
        wait_rate = seconds(wait_rate)
    # Shortest paths use only supplied directed edges, never unknown zero costs.
    paths = {}
    places = {location, *(p for m in methods for p in m['route'])}
    for edge in context.get('travel', []):
        a, b, cost = edge['source'], edge['destination'], seconds(edge['seconds'])
        places.update((a, b))
        if (a, b) not in paths or cost < paths[a, b][0]:
            paths[a, b] = cost, [a, b]
    paths.update({(p, p): (0., [p]) for p in places})
    for via in sorted(places):
        for a in sorted(places):
            for b in sorted(places):
                if (a, via) in paths and (via, b) in paths:
                    cost = paths[a, via][0] + paths[via, b][0]
                    if (a, b) not in paths or cost < paths[a, b][0]:
                        paths[a, b] = cost, paths[a, via][1] + paths[via, b][1][1:]
    switches = {}
    for switch in context.get('switches', []):
        key = switch['key'], switch['from'], switch['to'], switch['at']
        switches[key] = seconds(switch['seconds'])
    missing_routes, missing_states, missing_switches = set(), set(), set()
    missing_processes, missing_wait_costs = set(), set()
    required = frozenset(set(dependencies) - optional)
    buckets = [dict() for _ in range(len(dependencies) + 1)]
    buckets[0][(frozenset(), location, tuple(sorted(state.items())),
                tuple(sorted(processes.items())), None)] = (0., [])
    expanded = pruned = 0
    finalists, best_coverage = [], -1
    chosen = (notes.get('active') or {}).get('method_id')
    chosen_step = None
    for bucket in buckets:
        ranked = sorted(bucket.items(), key=lambda row: row[1][0])
        pruned += max(0, len(ranked) - beam_width)
        # Optional support work is charged, not counted as a required outcome.
        # Compare completed plans across depths: a shorter method may need no setup.
        for candidate in ranked[:beam_width]:
            coverage = len(candidate[0][0] & required)
            if coverage > best_coverage:
                finalists, best_coverage = [], coverage
            if coverage == best_coverage:
                finalists.append(candidate)
        finalists = sorted(finalists, key=lambda row: row[1][0])[:beam_width]
        for (done, at, values, clocks, first), (cost, steps) in ranked[:beam_width]:
            if required <= done:
                continue
            expanded += 1
            current = dict(values)
            remaining = dict(clocks)
            for method in methods:
                achieved = frozenset(method['goals'])
                if (achieved & done or not method['depends_on'] <= done
                        or any(not dependencies[g] <= done for g in achieved)):
                    continue
                unknown = set(method['awaits']) - remaining.keys()
                if unknown:
                    missing_processes.update((method['id'], p) for p in unknown)
                    continue
                wait = max((remaining[p] for p in method['awaits']), default=0.)
                if wait and wait_rate is None:
                    missing_wait_costs.add(method['id'])
                    continue
                unmet = {k for k, v in method['requires'].items() if current.get(k) != v}
                if unmet:
                    missing_states.update((method['id'], k, method['requires'][k]) for k in unmet)
                    continue
                waypoints = [at, *method['route']]
                edges = list(zip(waypoints, waypoints[1:]))
                absent = [edge for edge in edges if edge not in paths]
                if absent:
                    missing_routes.update(absent)
                    continue
                setup_cost, changes = 0., []
                for key, value in method['setup'].items():
                    if current.get(key) == value:
                        continue
                    switch = key, current.get(key), value, method['route'][0]
                    if switch not in switches:
                        missing_switches.add(switch)
                        break
                    setup_cost += switches[switch]
                    changes.append(dict(key=key, before=current.get(key), after=value))
                else:
                    after = {**current, **method['setup'], **method['effects']}
                    travel_cost = sum(paths[edge][0] for edge in edges)
                    route = [at]
                    for edge in edges:
                        route.extend(paths[edge][1][1:])
                    parts = dict(work=method['seconds'], travel=travel_cost,
                                 setup=setup_cost, recovery=method['recovery_seconds'])
                    if method['awaits']:
                        parts['wait'] = wait * (wait_rate or 0.)
                    total = sum(parts.values())
                    step = dict(method_id=method['id'], goals=method['goals'], route=route,
                                setup_changes=changes, costs_seconds=parts, estimated_seconds=total)
                    elapsed = wait + method['simulation_seconds']
                    after_clocks = {p: max(0., t - elapsed) for p, t in clocks}
                    after_clocks.update(method['starts'])  # Start at this method's end.
                    if clocks or method['starts'] or method['awaits']:
                        step['process_timing'] = dict(wait_simulation_seconds=wait,
                            work_simulation_seconds=method['simulation_seconds'],
                            remaining_simulation_seconds=after_clocks)
                    if not done and method['id'] == chosen:
                        chosen_step = step
                    reached = done | achieved
                    index = (reached, route[-1], tuple(sorted(after.items())),
                             tuple(sorted(after_clocks.items())), first or method['id'])
                    target = buckets[len(reached)]
                    if index not in target or cost + total < target[index][0]:
                        target[index] = (cost + total, [*steps, step])
    (done, _, _, _, _), (cost, steps) = finalists[0]
    complete = required <= done
    comparisons, firsts = [], set()
    chosen_comparison = None
    for (covered, _, _, _, _), (total, path) in finalists:
        # Equal node counts can cover different work in an incomplete DAG.
        # Such paths are choices, but their cost difference is not a saving.
        if covered & required != done & required:
            continue
        if path and path[0]['method_id'] not in firsts:
            firsts.add(path[0]['method_id'])
            comparison = dict(first_method=path[0]['method_id'], estimated_seconds=total,
                              first_method_costs_seconds=path[0]['costs_seconds'],
                              method_order=[s['method_id'] for s in path])
            if len(comparisons) < 3:
                comparisons.append(comparison)
            if path[0]['method_id'] == chosen:
                chosen_comparison = comparison
    missing = sorted(required - {g for m in methods for g in m['goals']})
    result = dict(status='estimated' if complete else 'partial' if steps else 'needs_revision',
        source='model_authored_beliefs_and_costs',
        scope='Modeled work only; beam search estimates, not a physical or optimality guarantee',
        coverage=dict(scheduled=len(done & required), unfinished=len(required), unplanned=sorted(required-done),
                      missing_methods=missing),
        optional_work=dict(available=sorted(set(dependencies) - required),
                           scheduled=sorted(done - required)),
        estimated_seconds=cost, steps=steps, next=steps[0] if steps else None,
        alternatives=comparisons, comparison_goals=sorted(done & required),
        expanded_states=expanded, pruned_states=pruned,
        verification_goals=[key for key, g in goals.items() if key not in dependencies and key not in optional
                            and g['status'] not in ('done', 'retired')])
    recommended = (result['next'] or {}).get('method_id')
    result['active'] = notes.get('active')
    result['selection'] = dict(method_id=chosen, recommended_method_id=recommended,
        matches_recommendation=chosen == recommended if chosen and recommended else None,
        ready_in_authored_state=chosen_step is not None if chosen else None,
        estimated_method_seconds=chosen_step['estimated_seconds'] if chosen_step else None,
        estimated_method_costs_seconds=chosen_step['costs_seconds'] if chosen_step else None,
        compared_remaining_seconds=chosen_comparison['estimated_seconds'] if chosen_comparison else None,
        extra_estimated_seconds=(chosen_comparison['estimated_seconds'] - cost)
                                if chosen_comparison else None,
        source='model_declared_selection_not_verified_action_alignment')
    if not complete:
        result['unplanned_results'] = [dict(id=key, goal=goals[key].get('goal', key),
            prerequisites=sorted(dependencies[key]), missing_method=key in missing)
            for key in sorted(required-done)]
        result['gaps'] = dict(routes=[list(x) for x in sorted(missing_routes)],
            required_states=[list(x) for x in sorted(missing_states)],
            setup_transitions=[list(x) for x in sorted(missing_switches, key=str)],
            processes=[list(x) for x in sorted(missing_processes)],
            wait_cost_methods=sorted(missing_wait_costs))
    return result


def task_advice(content):
    """Parse a single goal tree; malformed planning never blocks execution."""
    try:
        notes = json.loads(content)
    except (ValueError, TypeError):
        notes = None
    if not isinstance(notes, dict) or not isinstance(notes.get('planning'), dict):
        return dict(status='needs_model', reason='Add methods to executable goals and planning location/state. '
                    'Include known work across branches, not only the active item; unknown work stays explicit.')
    try:
        return schedule_task(notes)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        return dict(status='needs_revision', reason=str(exc))
