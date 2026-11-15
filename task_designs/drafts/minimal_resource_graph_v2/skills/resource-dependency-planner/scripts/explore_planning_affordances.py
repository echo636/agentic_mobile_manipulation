"""Exact sensitivity to optimistic box cutting and batch unloading semantics."""
import argparse
import heapq
import json
from pathlib import Path

import explore_processing_transport as core
import check_planning_action_relaxation as cut_relaxation


def choices(state, task, *, box_cut, pour, site=None, payload=True):
    result = (cut_relaxation.choices if box_cut else core.choices)(state, task)
    if pour and state[8] == 2 and (state[3] or state[6] or state[7] == 2):
        after = list(state)
        after[1 + state[0]] += state[3]
        after[3] = 0
        after[4 + state[0]] += state[6]
        after[6] = 0
        if state[7] == 2:
            after[7] = state[0]
        core.invariant(tuple(after), task)
        result['pour_all'] = (tuple(after), 1)
    return {a: v for a, v in result.items()
            if not (site is not None and a in ('cut', 'cut_in_box') and state[0] != site)
            and not (not payload and a in ('put_raw_box', 'put_product_box', 'put_tool_box'))}


def solve(task, **options):
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
            actions = path[::-1]
            state, checked = start, 0
            for action in actions:
                state, weight = choices(state, task, **options)[action]
                checked += weight
            assert core.goal(state, task) and checked == cost
            return dict(cost=cost, actions=actions)
        for action, (after, weight) in choices(state, task, **options).items():
            candidate = cost + weight
            if candidate < best.get(after, float('inf')):
                best[after] = candidate
                parents[after] = (state, action)
                heapq.heappush(queue, (candidate, after))
    raise ValueError('Unexpected unreachable task')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--screen', type=Path, action='append', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    assert not args.output.exists()
    rows = []
    for screen in args.screen:
        for path in sorted((screen / 'cases').glob('*/task.json')):
            record = json.loads(path.read_text())
            if 'family' in record and record['family'] != 'processing':
                continue
            task = core.Task(**record.get('parameters', record))
            reference = json.loads(path.with_name('reference.json').read_text())
            base = reference.get('optimal', reference)['cost']
            variants = {}
            for label, box_cut, pour in [('box_cut', True, False), ('pour', False, True), ('both', True, True)]:
                variants[label] = solve(task, box_cut=box_cut, pour=pour)
                assert variants[label]['cost'] <= base
            variants['both_source_cut'] = solve(task, box_cut=True, pour=True, site=0)
            variants['both_destination_cut'] = solve(task, box_cut=True, pour=True, site=1)
            variants['both_no_payload'] = solve(task, box_cut=True, pour=True, payload=False)
            rows.append(dict(case=task.name, parameters=record.get('parameters', record),
                             frozen_optimum=base, variants=variants))
            print(task.name, base, {k: v['cost'] for k, v in variants.items()}, flush=True)
    core.write(args.output, dict(
        scope='Symbolic sensitivity, not physical evidence; unlimited receiving surfaces, retained cut products, abstract equal slots and perfect one-action all-payload pouring are assumptions',
        sources={p.name: core.sha(p) for p in (Path(__file__), Path(core.__file__), Path(cut_relaxation.__file__))},
        rows=rows))


if __name__ == '__main__':
    main()
