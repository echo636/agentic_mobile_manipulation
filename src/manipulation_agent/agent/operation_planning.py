"""Optimize explicit operation DAGs while keeping task results separate.

Graph substitutions are model-proposed alternatives, not simulator-derived
capabilities. The existing state/cost search schedules their individual
operations; no predicted effect updates the agent's observed goal status.
"""
import copy
import json

from .transport_planning import ready_goals


def operation_map(rows):
    nodes = {row['id']: copy.deepcopy(row) for row in rows}
    if len(nodes) != len(rows) or '__execution__' in nodes:
        raise ValueError('Operations need unique IDs distinct from __execution__')
    ready_goals({key: set(row.get('depends_on', [])) for key, row in nodes.items()}, set())
    return nodes


def replace_subgraph(nodes, rewrite):
    """Preserve boundary dependencies when replacing a pending subgraph."""
    removed = set(rewrite['replaces'])
    if not removed or not removed <= nodes.keys():
        raise ValueError('A rewrite must replace existing operation IDs')
    if any(nodes[key].get('status', 'pending') != 'pending' for key in removed):
        raise ValueError('Only pending operations may be replaced')
    added = operation_map(rewrite['operations'])
    if not added or added.keys() & (nodes.keys() - removed):
        raise ValueError('Replacement operations need distinct IDs and a nonempty DAG')
    before = {g for key in removed for g in nodes[key].get('achieves', [])}
    after = {g for row in added.values() for g in row.get('achieves', [])}
    if before != after:
        raise ValueError('A replacement must preserve the same declared task results')
    incoming = {dep for key in removed for dep in nodes[key].get('depends_on', [])} - removed
    tails = added.keys() - {dep for row in added.values() for dep in row.get('depends_on', [])}
    for row in added.values():
        if not row.get('depends_on'):
            row['depends_on'] = sorted(incoming)
    result = {key: copy.deepcopy(row) for key, row in nodes.items() if key not in removed}
    for row in result.values():
        deps = set(row.get('depends_on', []))
        if deps & removed:
            row['depends_on'] = sorted((deps - removed) | tails)
    return operation_map(list(result.values()) + list(added.values()))


def shared_operations(nodes):
    """Merge only explicitly equivalent, pending shared work, preserving edges.

    A share label means the same occurrence, not merely the same primitive.
    Repeated cuts, observations at different times, and consumptive work must
    never receive such a label. A cyclic merge is not an admissible variant.
    """
    groups = {}
    for key, row in nodes.items():
        if row.get('share') and row.get('status', 'pending') == 'pending':
            signature = json.dumps({k: v for k, v in row.items()
                if k not in {'id', 'description', 'check', 'depends_on', 'achieves'}}, sort_keys=True)
            groups.setdefault(signature, []).append(key)
    aliases = {key: keys[0] for keys in groups.values() if len(keys) > 1 for key in keys}
    if not aliases:
        return None
    result = {}
    for key, row in nodes.items():
        canonical = aliases.get(key, key)
        merged = result.setdefault(canonical, {**copy.deepcopy(row), 'id': canonical,
                                               'depends_on': [], 'achieves': []})
        merged['depends_on'] = sorted(set(merged['depends_on']) |
            {aliases.get(dep, dep) for dep in row.get('depends_on', [])})
        merged['achieves'] = sorted(set(merged['achieves']) | set(row.get('achieves', [])))
    try:
        return operation_map(list(result.values()))
    except ValueError:
        return None


def graph_candidates(notes, limit=32):
    base = operation_map(notes['execution']['operations'])
    variants = [dict(id='base', nodes=base, changes=[])]
    truncated = False
    rewrites = notes['execution'].get('rewrites', [])
    if len({r['id'] for r in rewrites}) != len(rewrites):
        raise ValueError('Rewrite IDs must be unique')
    for rewrite in rewrites:
        for variant in list(variants):
            if not set(rewrite['replaces']) <= variant['nodes'].keys():
                continue  # Overlapping alternatives cannot both be applied.
            if any(variant['nodes'][key].get('status', 'pending') != 'pending'
                   for key in rewrite['replaces']):
                continue  # Progress makes earlier alternatives inapplicable.
            changed = replace_subgraph(variant['nodes'], rewrite)
            if len(variants) >= limit:
                truncated = True
                continue
            variants.append(dict(id=variant['id'] + '/' + rewrite['id'], nodes=changed,
                changes=[*variant['changes'], dict(kind='replacement', id=rewrite['id'],
                    description=rewrite['description'], replaces=rewrite['replaces'],
                    adds=[row['id'] for row in rewrite['operations']])]))
    for variant in list(variants):
        merged = shared_operations(variant['nodes'])
        if merged is None:
            continue
        if len(variants) >= limit:
            truncated = True
            continue
        variants.append(dict(id=variant['id'] + '/shared', nodes=merged,
            changes=[*variant['changes'], dict(kind='shared_work',
                removed_operations=sorted(variant['nodes'].keys() - merged.keys()))]))
    return variants, truncated


def select_operation(notes, choice):
    """Materialize the chosen graph before recording its next RGB operation."""
    if 'operators' in notes['execution']:
        from .action_search import select_action_graph
        return select_action_graph(notes, choice)
    variants, _ = graph_candidates(notes)
    selected = next(v for v in variants if v['id'] == choice['plan_id'])
    operation = selected['nodes'][choice['operation_id']]
    if operation.get('status', 'pending') != 'pending':
        raise ValueError('Select a pending operation')
    graph = notes['execution']
    graph['operations'] = list(selected['nodes'].values())
    graph['rewrites'] = [r for r in graph.get('rewrites', [])
                        if set(r['replaces']) <= selected['nodes'].keys() and
                        not any(c.get('id') == r['id'] for c in selected['changes'])]
    graph['last_changes'] = selected['changes']
    notes['active'] = dict(operation_id=operation['id'],
        serves=operation.get('achieves', []), steps=choice['steps'], next_check=choice['next_check'])
    return notes


def operation_advice(notes):
    if 'operators' in notes['execution']:
        from .action_search import action_advice
        return action_advice(notes)
    # Reuse the resource/location/process-clock scheduler; synthetic work IDs
    # are operation IDs, never the task's goal IDs.
    from .task_planning import compile_goals, schedule_task
    goals, required, _, optional = compile_goals(notes['goals'])
    required = set(required) - optional
    variants, truncated = graph_candidates(notes)
    candidates = []
    selected_id = (notes.get('active') or {}).get('operation_id')
    for variant in variants:
        nodes = variant['nodes']
        modeled = {g for row in nodes.values() for g in row.get('achieves', [])}
        if modeled - goals.keys():
            raise ValueError('Operation achieves entries must reference task goal IDs')
        work = [dict(id='__execution__', parent_id=None, status='pending')]
        for key, op in nodes.items():
            method = {k: op[k] for k in ('seconds', 'requires', 'effects', 'recovery_seconds',
                'simulation_seconds', 'starts', 'awaits') if k in op}
            method.update(id=key, description=op['description'], route=[op['at']])
            work.append(dict(id=key, parent_id='__execution__', status=op.get('status', 'pending'),
                goal=op['description'], check=op.get('check'),
                depends_on=op.get('depends_on', []), methods=[method]))
        plan = schedule_task(dict(goals=work, planning=notes['planning'],
                                  active=dict(method_id=selected_id)))
        covered = {g for s in plan['steps'] for g in nodes[s['method_id']].get('achieves', [])} & required
        candidates.append(dict(variant=variant, plan=plan, covered=covered, modeled=modeled,
                               complete=plan['status'] == 'estimated'))
    candidates.sort(key=lambda c: (-c['complete'], -len(c['covered']), c['plan']['estimated_seconds']))
    best = candidates[0]
    variant, plan = best['variant'], best['plan']
    nodes = variant['nodes']

    def step(value):
        value = copy.deepcopy(value)
        key = value.pop('method_id')
        value['operation_id'] = key
        value['goals'] = nodes[key].get('achieves', [])
        value['action'] = nodes[key].get('action')
        return value

    comparable = [c for c in candidates if c['complete'] and c['covered'] == best['covered']]
    schedules = [dict(plan_id=c['variant']['id'], changes=c['variant']['changes'],
        estimated_seconds=path['estimated_seconds'], operation_order=path['method_order'],
        next_operation=path['first_method'])
        for c in comparable for path in c['plan']['alternatives']]
    schedules.sort(key=lambda row: row['estimated_seconds'])
    representatives = {}
    for schedule in schedules:
        representatives.setdefault(schedule['plan_id'], schedule)
    alternatives = list(representatives.values())[:3]
    alternatives += [s for s in schedules if s not in alternatives][:3-len(alternatives)]
    alternatives.sort(key=lambda row: row['estimated_seconds'])
    # The selected operation may remain executable in the current graph even
    # when another graph has become cheaper. Preserve its actual context.
    selected_plan = next(c['plan'] for c in candidates if c['variant']['id'] == 'base')
    chosen = selected_plan.get('selected_method')
    if chosen:
        op = notes['execution']['operations']
        op = next(row for row in op if row['id'] == selected_id)
        chosen = {**chosen, 'operation_id': chosen['method_id'], 'action': op.get('action'),
            'check': op.get('check'), 'results': [dict(id=g, goal=goals[g].get('goal', g),
                check=goals[g].get('check')) for g in op.get('achieves', [])]}
        chosen.pop('method_id')
    selection = {key.replace('method', 'operation'): value
                 for key, value in selected_plan['selection'].items()}
    result = dict(status='estimated' if best['complete'] and required <= best['covered'] else 'partial',
        representation='operation-dag-v1', source='model_authored_operations_and_costs',
        scope='Cost of explicit operation graphs; preserves declared results, not proof of physical equivalence',
        active=notes.get('active'), selected_operation=chosen, selection=selection,
        plan_id=variant['id'], graph_changes=variant['changes'],
        coverage=dict(scheduled=len(best['covered']), unfinished=len(required),
                      unplanned=sorted(required - best['covered']),
                      missing_operations=sorted(required - best['modeled']),
                      operations_scheduled=len(plan['steps']),
                      operations_remaining=plan['coverage']['unfinished'],
                      operations_unplanned=plan['coverage']['unplanned']),
        operation_graph=[dict(id=k, description=v['description'], action=v.get('action'), at=v['at'],
            depends_on=v.get('depends_on', []), achieves=v.get('achieves', []),
            status=v.get('status', 'pending')) for k, v in nodes.items()],
        steps=[step(s) for s in plan['steps']], next=step(plan['next']) if plan['next'] else None,
        estimated_seconds=plan['estimated_seconds'], alternatives=alternatives,
        comparison=dict(graphs_considered=len(candidates), graph_search_truncated=truncated,
            complete_comparable_graphs=len(comparable),
            retained_comparable_schedules=len(schedules),
            baseline_estimated_seconds=selected_plan['estimated_seconds']
                if selected_plan['status'] == 'estimated' else None,
            estimated_cost_range_seconds=[min(s['estimated_seconds'] for s in schedules),
                max(s['estimated_seconds'] for s in schedules)] if schedules else None,
            scope='Same modeled results, complete graph schedules only; beam search is not a global optimality proof'),
        expanded_states=sum(c['plan']['expanded_states'] for c in candidates),
        pruned_states=sum(c['plan']['pruned_states'] for c in candidates))
    if plan.get('gaps'):
        result['gaps'] = plan['gaps']
    result['unplanned_results'] = [dict(id=g, goal=goals[g].get('goal', g))
                                  for g in sorted(required - best['covered'])]
    return result
