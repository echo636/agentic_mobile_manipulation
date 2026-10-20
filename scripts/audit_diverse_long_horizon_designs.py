#!/usr/bin/env python3
"""Audit proposal arithmetic, references and macro DAGs, not runtime feasibility."""
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


def unique(records, key):
    result = {record[key]: record for record in records}
    require(len(result) == len(records), f'Duplicate {key}')
    return result


def audit(catalog, workspace_root=None):
    require(catalog['kind'] == 'diverse_long_horizon_design_catalog', 'Wrong catalog kind')
    require(catalog['schema_version'] == 1, 'Unsupported schema version')
    require(catalog['status'] == 'design_only' and catalog['benchmark_ready'] is False,
            'Static proposals must remain explicitly unvalidated')
    protocol = catalog['counting_protocol']
    require(protocol['measured_minimum_proven'] is False, 'No minimum-action proof exists')
    require(protocol['individual_handling'] is True and protocol['enforcement_status'] == 'planned',
            'Handling constraints are conditional and not yet enforced')
    require(protocol['target_minimum'] == 100, 'This cohort targets 100 useful calls')

    # Read the real action names without importing simulator or client dependencies.
    tree = ast.parse((REPO/'src/manipulation_agent/executors/primitives.py').read_text())
    primitives = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'PRIMITIVES'
                                               for t in node.targets):
            primitives = set(ast.literal_eval(node.value))
    require(bool(primitives), 'Missing public primitive catalog')
    countable = primitives - {'navigate_to', 'wait', 'release'}
    references = unique(catalog['evidence']['sources'], 'task')
    for ref in references.values():
        require(ref['pinned_test_instance_verified'] is False
                and ref['new_scene_or_asset_model_verified'] is False,
                'Source references are not new-asset acceptance')
    provenance_status = 'not_checked_without_workspace_root'
    if workspace_root is not None:
        evidence = catalog['evidence']
        manifest_path = workspace_root/evidence['challenge_manifest_path']
        require(hashlib.sha256(manifest_path.read_bytes()).hexdigest()
                == evidence['challenge_manifest_sha256'], 'Manifest hash changed')
        manifest = {t['id']: t for t in json.loads(manifest_path.read_text())}
        for name, ref in references.items():
            require(hashlib.sha256((workspace_root/ref['bddl_path']).read_bytes()).hexdigest()
                    == ref['bddl_sha256'], 'BDDL reference changed: ' + name)
            require(ref['challenge_manifest_listed'] == (name in manifest),
                    'Incorrect manifest membership: ' + name)
            require(ref['manifest_scene'] == manifest.get(name, {}).get('scene_model'),
                    'Incorrect manifest scene: ' + name)
        require(hashlib.sha256((REPO/evidence['historical_action_document']).read_bytes()).hexdigest()
                == evidence['historical_action_document_sha256'], 'Historical evidence document changed')
        provenance_status = 'source_hashes_and_manifest_metadata_passed'

    tasks = unique(catalog['tasks'], 'id')
    require(bool(tasks), 'Empty catalog')
    results = []
    for key, task in tasks.items():
        require(task['status'] == 'design_only' and task['benchmark_ready'] is False
                and task['measured_useful_calls'] is None, 'Unvalidated design status changed: ' + key)
        require(set(task['validation'].values()) == {'planned'}, 'Runtime validation must remain planned')
        require(task['evaluator_contract']['implementation_status'] == 'planned',
                'Proposal text is not an implemented evaluator')
        require(set(task['source_tasks']) <= references.keys(), 'Unknown source task: ' + key)
        require(task['priority'] in {'build_next','composition_gate','research_gate'}, 'Unknown priority')
        groups = unique(task['object_groups'], 'id')
        for group in groups.values():
            require(type(group['count']) is int and group['count'] > 0, 'Invalid entity count')
            require(group['source_task'] in task['source_tasks'], 'Undeclared group source')
            if 'generated_from' in group:
                require(group['generated_from'] in groups, 'Unknown lineage parent')
                require(type(group['outputs_per_parent']) is int and group['outputs_per_parent'] > 0,
                        'Invalid proposed output ratio')
                require(group['count'] == groups[group['generated_from']]['count'] * group['outputs_per_parent'],
                        'Inconsistent proposed lineage cardinality')
        phases = unique(task['phases'], 'id')
        pending, depths = dict(phases), {}
        while pending:
            ready = [p for p in pending if set(pending[p]['after']) <= depths.keys()]
            require(bool(ready), 'Cycle or missing phase dependency: ' + key)
            for p in ready:
                stage = pending.pop(p)
                require(stage['dependency_scope'] in {'global','per_item','per_batch'}, 'Unknown phase scope')
                require(bool(stage['independent_witness_zh']), 'Missing planned state witness')
                depths[p] = 1 + max((depths[d] for d in stage['after']), default=0)
        ledger = unique(task['useful_action_ledger'], 'id')
        counts, used, row_totals, signatures = Counter(), set(), {}, set()
        for row_id, row in ledger.items():
            require(row['phase'] in phases and row['group'] in groups, 'Unknown phase/group in ledger')
            actions = row['actions_per_entity']
            require(bool(actions) and set(actions) <= countable, 'Unknown, excluded or empty action sequence')
            require(len(set(actions)) == len(actions), 'Repeated primitive inside a unit needs a separate justification')
            require(bool(row['necessity_zh']), 'Missing semantic reason for counted actions')
            signature = (row['phase'], row['group'], tuple(actions))
            require(signature not in signatures, 'Duplicate action row')
            signatures.add(signature)
            if 'source_zone' in row or 'destination_zone' in row:
                require(row.get('source_zone') and row.get('destination_zone')
                        and row['source_zone'] != row['destination_zone'], 'Empty or identical transfer endpoints')
                require(len(actions) == 2 and actions[0] == 'grasp'
                        and actions[1] in {'place_on_top','place_inside','place_next_to','hang'},
                        'Transfer action sequence inconsistent with declared endpoints')
            group = groups[row['group']]
            require(not group.get('ledger_exempt', False), 'Exempt control objects cannot inflate ledger')
            used.add(row['group'])
            row_totals[row_id] = group['count'] * len(actions)
            for action in actions:
                counts[action] += group['count']
        require(used == {g for g in groups if not groups[g].get('ledger_exempt',False)}, 'Unused inventory group')
        total = sum(counts.values())
        require(total == task['declared_useful_calls'] and total >= 100, 'Incorrect or short action ledger: ' + key)
        for field, minimum in [('reference_orders',2), ('negative_cases',3)]:
            cases = task[field]
            require(len(cases) >= minimum and all(c['status'] == 'planned' for c in cases),
                    'Missing planned acceptance cases')
            require(len({c['description_zh'] for c in cases}) == len(cases), 'Duplicate acceptance case')
        for field in ['terminal_zh','historical_zh','invariants_zh']:
            require(bool(task['evaluator_contract'][field]), 'Missing evaluator requirements')
        require(bool(task['capability_gates_zh']) and bool(task['necessary_but_uncounted_actions_zh']),
                'Missing capability or count limitations')
        results.append(dict(task=key, family=task['task_family'], priority=task['priority'],
                            conditional_useful_calls=total, actions=dict(sorted(counts.items())),
                            row_counts=row_totals, macro_phases=len(phases),
                            macro_dag_depth=max(depths.values()), physical_status='planned', benchmark_ready=False))
    return dict(status='passed', validation_level='static_design_arithmetic_macro_dag_and_references_only',
                provenance_status=provenance_status, tasks=results,
                distinct_scene_candidates=len({t['scene_candidate'] for t in tasks.values()}),
                distinct_task_families=len({t['task_family'] for t in tasks.values()}),
                runtime_minimum_proven=False, benchmark_ready=False,
                limitations=['No simulator execution, source-instance binding, schedule execution or physical feasibility check.',
                             'Textual witnesses and constraints are requirements, not implemented runtime predicates.',
                             'This audit does not prove object flow, single-hand schedules, material compatibility or absence of shortcuts.',
                             'Macro DAG depth is descriptive; per-item/per-batch edges are not all-object barriers.'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('catalog', type=Path)
    parser.add_argument('--workspace-root', type=Path, help='Also verify exact local source references and manifest hashes')
    args = parser.parse_args()
    try:
        result = audit(json.loads(args.catalog.read_text()), args.workspace_root)
    except (KeyError, TypeError, ValueError, OSError) as exc:
        print(json.dumps(dict(status='failed', error=str(exc), benchmark_ready=False)))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
