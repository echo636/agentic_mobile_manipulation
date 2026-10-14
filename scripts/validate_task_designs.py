#!/usr/bin/env python3
"""Validate proposed goal DAGs on CPU; never claims scene or task feasibility."""
import argparse
from functools import lru_cache
import json
from pathlib import Path


def inspect(task):
    nodes = task['nodes']
    ids = [node['id'] for node in nodes]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{task['id']}: duplicate node ID")
    positions = {key: i for i, key in enumerate(ids)}
    masks = []
    for node in nodes:
        if node['kind'] not in {'milestone', 'terminal'}:
            raise ValueError('Unknown goal kind')
        parents = node['requires']
        if len(parents) != len(set(parents)) or any(p not in positions for p in parents):
            raise ValueError('Duplicate or unresolved dependency')
        if node['id'] in parents:
            raise ValueError('Self dependency')
        if not node['condition'] or set(node['dependency_reasons']) != set(parents):
            raise ValueError('Every dependency needs an explicit reason')
        masks.append(sum(1 << positions[p] for p in parents))
    remaining = set(ids)
    depth = {}
    while remaining:
        ready = [n for n in nodes if n['id'] in remaining and
                 all(p in depth for p in n['requires'])]
        if not ready:
            raise ValueError(f"{task['id']}: dependency cycle")
        for n in ready:
            depth[n['id']] = 1 + max((depth[p] for p in n['requires']), default=0)
            remaining.remove(n['id'])
    final = task['final_nodes']
    if not final or set(final) != {n['id'] for n in nodes if n['kind'] == 'terminal'}:
        raise ValueError('Final nodes must list precisely the terminal goals')
    if len(ids) > 20:
        raise ValueError('Exact design metrics limited to 20 nodes')
    all_done = (1 << len(ids)) - 1
    maximum_ready = 0

    @lru_cache(None)
    def orders(done):
        nonlocal maximum_ready
        if done == all_done:
            return 1
        ready = [i for i, needed in enumerate(masks)
                 if not done & (1 << i) and done & needed == needed]
        maximum_ready = max(maximum_ready, len(ready))
        return sum(orders(done | (1 << i)) for i in ready)

    count = orders(0)
    if count < 2 or maximum_ready < 2:
        raise ValueError('Design must allow more than one subgoal ordering')
    return {'task': task['id'], 'status': 'passed', 'l2_nodes': len(ids),
            'dependency_edges': sum(len(n['requires']) for n in nodes),
            'critical_path_nodes': max(depth.values()),
            'maximum_ready_nodes': maximum_ready, 'topological_orders': count,
            'scope': 'graph structure only; no grounding, physics, evaluator or autonomous validation'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('spec', type=Path)
    args = parser.parse_args()
    document = json.loads(args.spec.read_text())
    results = [inspect(task) for task in document['tasks']]
    print(json.dumps({'status': 'passed', 'validation_level': 'static_DAG_only',
                      'runtime_ready': False, 'tasks': results}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
