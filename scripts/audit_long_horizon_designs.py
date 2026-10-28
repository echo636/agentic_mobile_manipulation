#!/usr/bin/env python3
"""Check proposal flow and arithmetic; never certify simulator feasibility."""
import argparse
import json
from pathlib import Path


def audit(catalog):
    if catalog['kind'] != 'long_horizon_design_catalog' or catalog['benchmark_ready']:
        raise ValueError('Expected an explicitly unvalidated design catalog')
    protocol = catalog['counting_protocol']
    if protocol['ledger_actions_per_individual_transfer'] != ['grasp', 'place_on_top']:
        raise ValueError('This ledger requires the declared individual-transfer protocol')
    if protocol['measured_minimum_proven']:
        raise ValueError('Static design arithmetic is not a measured minimum')
    tasks = catalog['tasks']
    if len({task['id'] for task in tasks}) != len(tasks):
        raise ValueError('Duplicate task identifiers')
    results = []
    for task in tasks:
        if task['status'] != 'design_only' or task['benchmark_ready']:
            raise ValueError('Design status must remain explicit: ' + task['id'])
        locations = {}
        for group in task['object_groups']:
            if type(group['count']) is not int or group['count'] < 1:
                raise ValueError('Invalid inventory count')
            for index in range(1, group['count'] + 1):
                item = f'{group["id"]}_{index:02d}'
                if item in locations:
                    raise ValueError('Duplicate item: ' + item)
                locations[item] = group['initial_zone']
        aliases = task.get('zone_aliases', {})

        def canonical(zone):
            seen = set()
            while zone in aliases:
                if zone in seen:
                    raise ValueError('Cyclic physical-zone aliases')
                seen.add(zone)
                zone = aliases[zone]
            return zone

        phases = {phase['id']: phase for phase in task['phases']}
        if len(phases) != len(task['phases']):
            raise ValueError('Duplicate phase identifiers')
        pending = dict(phases)
        ancestors, depths, last_move = {}, {}, {}
        transfers = 0
        while pending:
            ready = [key for key, phase in pending.items()
                     if set(phase['after']) <= set(ancestors)]
            if not ready:
                raise ValueError('Phase cycle or unknown dependency: ' + task['id'])
            for key in ready:
                phase = pending.pop(key)
                parents = phase['after']
                ancestors[key] = set(parents).union(*(ancestors[p] for p in parents))
                depths[key] = 1 + max((depths[p] for p in parents), default=0)
                used = set()
                for movement in phase['moves']:
                    source, target = movement['source'], movement['destination']
                    if canonical(source) == canonical(target) or not movement['items']:
                        raise ValueError('Empty or no-op movement: ' + key)
                    for item in movement['items']:
                        if item in used or item not in locations:
                            raise ValueError('Unknown or repeated phase item: ' + item)
                        if canonical(locations[item]) != canonical(source):
                            raise ValueError('Movement starts at wrong location: ' + item)
                        if item in last_move and last_move[item] not in ancestors[key]:
                            raise ValueError('Repeated transfer lacks phase dependency: ' + item)
                        used.add(item)
                        locations[item] = target
                        last_move[item] = key
                        transfers += 1
                if not used and not phase['uncounted_operations']:
                    raise ValueError('Phase has no semantic work: ' + key)
        calls = 2 * transfers
        expected = task['declared_length']
        if (transfers != expected['required_item_transfers']
                or calls != expected['conditional_grasp_place_calls']
                or calls < protocol['target_minimum']
                or expected['measured_successful_act_calls'] is not None):
            raise ValueError('Invalid or overstated length: ' + task['id'])
        if len(task['negative_cases']) < 3 or len(task['reference_orders']) < 2:
            raise ValueError('Missing planned acceptance cases')
        if set(last_move) != set(locations):
            raise ValueError('Inventory contains unused objects')
        results.append(dict(task=task['id'],inventory_objects=len(locations),
                            required_transfers=transfers,conditional_grasp_place_calls=calls,
                            phases=len(phases),longest_phase_chain=max(depths.values()),
                            physical_status='planned',benchmark_ready=False))
    return dict(status='passed',validation_level='static_design_flow_and_arithmetic_only',
                counting_assumptions=protocol['conditional_assumptions'],
                runtime_minimum_proven=False,benchmark_ready=False,tasks=results)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('catalog', type=Path)
    args = parser.parse_args()
    try:
        result = audit(json.loads(args.catalog.read_text()))
    except (KeyError, TypeError, ValueError, OSError) as exc:
        print(json.dumps(dict(status='failed',error=str(exc),benchmark_ready=False)))
        return 1
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
