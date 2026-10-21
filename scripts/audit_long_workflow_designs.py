#!/usr/bin/env python3
"""Check extended design accounting without certifying minimum length or physics."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import runpy

REPO = Path(__file__).resolve().parents[1]
base_audit = runpy.run_path(str(REPO/'scripts/audit_nonrepetitive_designs.py'))['audit']


def require(condition, text):
    if not condition:
        raise ValueError(text)


def audit(catalog, workspace=None):
    result = base_audit(catalog)
    require(catalog['revision'] == 'extended_v2', 'Wrong extension revision')
    require(catalog['minimum_operations_proven'] is False, 'No minimum-length proof exists')
    policy = catalog['extension_policy']
    require(policy['reference_ledger_not_lower_bound'] is True
            and policy['no_object_replication_for_length'] is True
            and policy['new_capabilities_must_pass_before_freeze'] is True,
            'Missing extension limitations')
    targets = catalog['target_reference_operation_range']
    require(targets == [100, 130], 'Unexpected reference-length design target')
    summaries = []
    for task in catalog['tasks']:
        require(task['minimum_operations_proven'] is False, 'Reference is not a lower bound')
        require(task['parent_design_id'] and task['ledger_scope'], 'Missing lineage or count scope')
        calls = Counter()
        phase_counts = {}
        for phase in task['phases']:
            rows = phase['conditional_action_ledger']
            names = [r['primitive'] for r in rows]
            require(len(set(names)) == len(names), 'Duplicate primitive row')
            require(set(names) == set(phase['primitives']), 'Ledger disagrees with phase primitives')
            require(not set(names) & {'navigate_to', 'wait', 'look', 'release'},
                    'Navigation, waiting, observation or unplanned release cannot pad the ledger')
            require(not phase['check_only'] or not rows, 'State checks are not actions')
            for row in rows:
                require(type(row['count']) is int and row['count'] > 0, 'Invalid action multiplicity')
                calls[row['primitive']] += row['count']
            phase_counts[phase['id']] = sum(row['count'] for row in rows)
        total = sum(calls.values())
        require(total == task['declared_conditional_operations'], 'Declared operation count is incorrect')
        require(targets[0] <= total <= targets[1], 'Reference ledger misses the declared design target')
        summaries.append(dict(id=task['id'], title_zh=task['title_zh'],
                              conditional_reference_operations=total, phases=phase_counts,
                              primitives=dict(sorted(calls.items())),
                              physical_status='planned', minimum_proven=False))
    native = json.loads((REPO/'task_designs/nonrepetitive_v2/native_sources.json').read_text())
    provenance = 'local_descriptor_hash_checked_only'
    for source in native:
        require(source['evidence_level'] == 'BDDL_source_only'
                and source['model_binding'] == source['physical_validation'] == 'planned',
                'BDDL is not an asset or capability acceptance')
        if workspace:
            path = (workspace/source['path']).resolve()
            source_root = (workspace/'repos/BEHAVIOR-1K/bddl3/bddl/activity_definitions').resolve()
            require(path.is_relative_to(source_root) and path.name == 'problem0.bddl', 'Invalid native source path')
            require(hashlib.sha256(path.read_bytes()).hexdigest() == source['sha256'], 'Native source changed')
    if workspace:
        provenance = 'BDDL_source_hashes_checked_not_models_or_physics'
    result.update(validation_level='static_extended_design_and_conditional_action_ledger_only',
                  native_provenance=provenance, operation_ledgers=summaries,
                  minimum_operations_proven=False, measured_lengths=None)
    result['limitations'] += [
        'Counts are action multisets for one proposed schedule, not a compiled executable reference.',
        'Valid batching or different layouts may shorten the route; no lower bound of 100 operations is proven.',
        'Furniture assembly, apple conversion, material compatibility and laundry remain capability gates.',
        'A failed capability gate requires redesign and recounting, never retention of the assumed operations.']
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('catalog', type=Path)
    parser.add_argument('--workspace-root', type=Path)
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
