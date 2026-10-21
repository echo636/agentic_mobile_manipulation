#!/usr/bin/env python3
"""Validate redesign structure and provenance, never physical task feasibility."""
import argparse
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def unique(rows, name='id'):
    result = {row[name]: row for row in rows}
    require(len(rows) == len(result), 'Duplicate ' + name)
    return result


def graph_depth(phases, *, physical_only=False):
    pending = dict(phases)
    depth = {}
    while pending:
        ready = [key for key, row in pending.items()
                 if all(edge['phase'] in depth for edge in row['requires'])]
        require(ready, 'Cycle or unknown phase dependency')
        for key in ready:
            row = pending.pop(key)
            if physical_only and row['check_only']:
                depth[key] = 0
            else:
                parents = [depth[edge['phase']] for edge in row['requires']
                           if not physical_only or edge['scope'] != 'reference_only']
                depth[key] = 1 + max(parents, default=0)
    return max(depth.values(), default=0)


def audit(catalog, repo=REPO):
    require(catalog['kind'] == 'nonrepetitive_long_horizon_redesign', 'Wrong catalog kind')
    require(catalog['schema_version'] == 1, 'Unsupported schema')
    require(catalog['status'] == 'design_only' and catalog['benchmark_ready'] is False,
            'Static design cannot claim physical readiness')
    policy = catalog['selection_policy']
    require(policy['retained_repetition_baselines'] == 1 and policy['padding_forbidden'] is True,
            'Exactly one repetition baseline must be retained')
    require(policy['target_minimum_calls'] is None, 'No fabricated action-length requirement')
    sources = unique(catalog['sources'])
    for source in sources.values():
        path = (repo / source['path']).resolve()
        require(path.is_relative_to(repo.resolve()), 'Source escapes repository')
        require(hashlib.sha256(path.read_bytes()).hexdigest() == source['sha256'],
                'Source hash changed: ' + source['id'])
    baseline = catalog['retained_baseline']
    require(baseline['source'] in sources and baseline['benchmark_ready'] is False,
            'Invalid retained baseline')
    frozen_recipe = json.loads((repo / sources[baseline['source']]['path']).read_text())
    require(frozen_recipe['id'] == baseline['id'], 'Retained baseline ID mismatch')
    primitive_tree = ast.parse((repo / 'src/manipulation_agent/executors/primitives.py').read_text())
    primitives = set()
    for node in primitive_tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'PRIMITIVES'
                                               for target in node.targets):
            primitives = set(ast.literal_eval(node.value))
    require(primitives, 'Missing real primitive catalog')
    tasks = unique(catalog['tasks'])
    require(len(tasks) == 4, 'This revision replaces four current tasks')
    expected = {sources[key]['path'] for key in ['garden', 'garage', 'bedroom', 'library']}
    require({task['replaces'] for task in tasks.values()} == expected, 'Incomplete or duplicate replacements')
    require({row['task'] for row in catalog['implementation_plan']} == set(tasks), 'Incomplete build priorities')
    results = []
    for task in tasks.values():
        require(task['status'] == 'design_only' and task['benchmark_ready'] is False
                and task['runtime_recipe'] is None and task['frozen_instance'] is None
                and task['measured_action_count'] is None, 'Unvalidated design claimed execution')
        require(set(task['validation'].values()) == {'planned'}, 'Design data cannot contain run passes')
        require(task['branch_type'] == 'required_partial_order_not_observation_conditional',
                'Conditional branching is not implemented by this catalog')
        require(task['public_instruction_zh'] and task['public_context_zh'], 'Missing public contract')
        groups = unique(task['object_groups'])
        for group in groups.values():
            require(type(group['count']) is int and group['count'] > 0, 'Invalid object count')
            require(group['count_reason_zh'] and group['source_role_or_locator']
                    and group['repetition_class'], 'Unexplained inventory')
            require(group['source'] in sources, 'Unknown object source')
            require(group['asset_binding_status'] == 'planned', 'Unverified asset binding claim')
        phases = unique(task['phases'])
        require(phases and 'complete' in phases, 'Missing complete check')
        for phase in phases.values():
            require(phase['witness_zh'] and phase['description_zh'], 'Missing state witness')
            require(set(phase['primitives']) <= primitives, 'Unknown primitive')
            require(type(phase['check_only']) is bool, 'Invalid check-only flag')
            require(bool(phase['primitives']) != phase['check_only'], 'Check/action node confused')
            require(len({edge['phase'] for edge in phase['requires']}) == len(phase['requires']),
                    'Duplicate dependency')
            for edge in phase['requires']:
                require(edge['phase'] in phases and edge['reason_zh'], 'Missing dependency or reason')
                require(edge['scope'] in {'global', 'per_item', 'reference_only'}, 'Unknown edge scope')
        depth = graph_depth(phases)
        orders = unique(task['reference_orders'])
        require(len(orders) >= 3, 'Need three macro schedules')
        stream_orders = set()
        for order in orders.values():
            require(order['status'] == 'planned', 'A symbolic order is not a physical pass')
            sequence = order['phases']
            require(len(sequence) == len(phases) and set(sequence) == set(phases), 'Incomplete schedule')
            position = {phase: i for i, phase in enumerate(sequence)}
            for phase in phases.values():
                require(all(position[edge['phase']] < position[phase['id']] for edge in phase['requires']),
                        'Schedule violates prerequisite: ' + phase['id'])
            stream_orders.add(tuple(phases[key]['stream'] for key in sequence))
        require(len(stream_orders) >= 3, 'Orders only relabel identical work')
        contract = task['evaluator_contract']
        require(contract['implementation_status'] == 'planned', 'Text is not a runtime evaluator')
        for field in ['terminal_zh', 'required_history_zh', 'persistent_guards_zh']:
            require(contract[field], 'Incomplete evaluator contract')
        negatives = unique(task['negative_cases'])
        require(len(negatives) >= 3, 'Missing physical negative-case plans')
        for case in negatives.values():
            require(case['status'] == 'planned' and case['rejection_witness_zh'], 'Invalid negative case')
            require(case['phases'] and set(case['phases']) <= set(phases), 'Unknown negative-case phase')
        require(task['capability_gates_zh'] and task['allowed_shortcuts_zh']
                and task['repetition_collapse_review_zh'], 'Missing feasibility or repetition review')
        edge_counts = Counter(edge['scope'] for phase in phases.values() for edge in phase['requires'])
        repeated_work = Counter()
        for group in groups.values():
            if group['kind'] == 'work_item':
                repeated_work[group['repetition_class']] += group['count']
        results.append(dict(id=task['id'], title_zh=task['title_zh'],
                            work_items=sum(g['count'] for g in groups.values() if g['kind'] == 'work_item'),
                            movable_objects_including_tools=sum(g['count'] for g in groups.values()),
                            repeated_work_classes={key: value for key, value in repeated_work.items() if value > 1},
                            macro_nodes=len(phases), check_only_nodes=sum(p['check_only'] for p in phases.values()),
                            edge_scopes=dict(edge_counts), reference_macro_depth=depth,
                            noncheck_nonreference_macro_depth=graph_depth(phases, physical_only=True),
                            distinct_reference_orders=len(stream_orders), measured_actions=None,
                            physics_status='planned'))
    return dict(status='passed', validation_level='static_redesign_structure_and_local_source_hashes_only',
                benchmark_ready=False, retained_repetition_baselines=1, redesigned_tasks=results,
                limitations=['No runtime recipe, frozen scene, compiled evaluator, physical execution or visual model result.',
                             'Source hashes identify reused definitions, not newly verified asset bindings.',
                             'Graph checks cannot prove realistic dependencies, capacity, single-hand schedules or task difficulty.',
                             'Reference-only edges and check-only nodes are explicitly separated; depth is not measured action length.',
                             'Narrative witnesses and persistent guards still need executable contracts and adversarial state tests.'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('catalog', type=Path)
    args = parser.parse_args()
    try:
        result = audit(json.loads(args.catalog.read_text()))
    except (KeyError, TypeError, ValueError, OSError) as exc:
        print(json.dumps(dict(status='failed', error=str(exc), benchmark_ready=False)))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
