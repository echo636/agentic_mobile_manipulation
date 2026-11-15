"""Check whether site-selection tradeoffs survive volume-conserving cuts."""
import argparse
from dataclasses import asdict
import heapq
from pathlib import Path

import explore_processing_transport as core
import explore_planning_affordances as affordance


def choices(state, task, site=None):
    # A raw item occupies two units, each half one, and the tool one.
    # Retain only states meeting this capacity; cutting conserves its usage.
    return {action: result for action, result in affordance.choices(
        state, task, box_cut=True, pour=True, site=site).items()
        if 2 * result[0][3] + result[0][6] + (result[0][7] == 2) <= task.capacity}


def solve(task, site=None):
    start = core.initial(task)
    queue, best, parents = [(0, start)], {start: 0}, {}
    while queue:
        cost, state = heapq.heappop(queue)
        if cost != best[state]:
            continue
        if core.goal(state, task):
            actions = []
            while state != start:
                state, action = parents[state]
                actions.append(action)
            actions.reverse()
            replay, checked = start, 0
            for action in actions:
                replay, weight = choices(replay, task, site)[action]
                checked += weight
            assert core.goal(replay, task) and checked == cost
            return dict(cost=cost, actions=actions)
        for action, (after, weight) in choices(state, task, site).items():
            if cost + weight < best.get(after, float('inf')):
                best[after] = cost + weight
                parents[after] = (state, action)
                heapq.heappush(queue, (cost + weight, after))
    raise ValueError('Unreachable task')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    assert not args.output.exists()
    rows = []
    for count in (1, 2):
        for capacity in (2, 3, 4, 6):
            for distance in (1, 2, 4, 6):
                for tool_start in (0, 1):
                    task = core.Task(f'n{count}_k{capacity}_d{distance}_t{tool_start}', count,
                                     capacity, distance, tool_start=tool_start)
                    results = {label: solve(task, site) for label, site in
                               [('global', None), ('source', 0), ('destination', 1)]}
                    rows.append(dict(parameters=asdict(task), results=results))
    core.write(args.output, dict(
        scope='64 volume-conserving symbolic instances, not native capacity measurements; perfect box cutting and batch pouring still assumed',
        raw_units=2, half_units=1, tool_units=1,
        source_sha256={p.name: core.sha(p) for p in
                       (Path(__file__), Path(core.__file__), Path(affordance.__file__))}, rows=rows))
    counts = {'source_strictly_better': 0, 'destination_strictly_better': 0, 'equal': 0,
              'mixed_strictly_better': 0}
    for row in rows:
        results = row['results']
        s, d, g = (results[x]['cost'] for x in ('source', 'destination', 'global'))
        counts['source_strictly_better' if s < d else 'destination_strictly_better' if d < s else 'equal'] += 1
        counts['mixed_strictly_better'] += g < min(s, d)
    print(counts)


if __name__ == '__main__':
    main()
