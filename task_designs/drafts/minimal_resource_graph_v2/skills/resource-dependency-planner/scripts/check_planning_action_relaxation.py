#!/usr/bin/env python3
"""Measure a symbolic cut-in-box relaxation; this is not a physics test."""
import argparse
import heapq
import json
from pathlib import Path

import explore_processing_transport as core


def choices(state, task):
    actions = core.choices(state, task)
    loc, raw_box, product_box, tool, box = state[0], state[3], state[6], state[7], state[8]
    # This relaxation optimistically keeps both products inside the resting box.
    # The original abstract one-object-per-slot capacity remains in force.
    if tool == 3 and box == loc and raw_box and raw_box + product_box < task.capacity:
        after = list(state)
        after[3] -= 1
        after[6] += 2
        core.invariant(tuple(after), task)
        actions['cut_in_box'] = (tuple(after), 1)
    return actions


def solve(task):
    start = core.initial(task)
    queue, best, parents = [(0, start)], {start: 0}, {}
    while queue:
        cost, state = heapq.heappop(queue)
        if cost != best[state]:
            continue
        if core.goal(state, task):
            path = []
            while state != start:
                state, action = parents[state]
                path.append(action)
            return cost, path[::-1]
        for action, (after, weight) in choices(state, task).items():
            candidate = cost + weight
            if candidate < best.get(after, float('inf')):
                best[after] = candidate
                parents[after] = (state, action)
                heapq.heappush(queue, (candidate, after))
    raise ValueError('Unexpected unreachable goal')


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--screen', type=Path, action='append', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    rows = []
    for screen in args.screen:
        for path in sorted((screen / 'cases').glob('*/task.json')):
            record = json.loads(path.read_text())
            if 'family' in record and record['family'] != 'processing':
                continue
            task = core.Task(**record.get('parameters', record))
            reference = json.loads(path.with_name('reference.json').read_text())
            base = reference.get('optimal', reference)['cost']
            relaxed, actions = solve(task)
            assert relaxed <= base
            state, cost = core.initial(task), 0
            for action in actions:
                state, weight = choices(state, task)[action]
                cost += weight
            assert core.goal(state, task) and cost == relaxed
            rows.append({'screen': str(screen), 'case': task.name, 'base_optimum': base,
                         'relaxed_optimum': relaxed, 'difference': relaxed - base,
                         'actions': actions, 'box_cuts': actions.count('cut_in_box')})
    core.write(args.output, {
        'scope': 'Optimistic discrete relaxation only; products remain in box by assumption, not physical evidence',
        'source_sha256': core.sha(Path(__file__)), 'rows': rows})
    for row in rows:
        print(row['case'], row['base_optimum'], row['relaxed_optimum'], row['box_cuts'])


if __name__ == '__main__':
    main()
