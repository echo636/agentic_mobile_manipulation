#!/usr/bin/env python3
"""Construct/freeze finite two-order tasks and compute offline reference plans.

Exact search is an evaluator, never exposed as a policy tool. Results certify
only the declared discrete model. Unknown/budget-exhausted searches stay unknown.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import heapq
from itertools import count
import json
from pathlib import Path
import shutil
import time

from manipulation_agent.tasks.order_fulfillment import (
    Contract, NAMES, RAWS, complete, initial, lower_bound, public_prompt, replay, transitions,
)

from manipulation_agent.references.order_fulfillment_methods import compile_methods

def solve(task, cut_sites=None, *, max_expanded=500_000, timeout=90, heuristic=True):
    start, serial = initial(task), count()
    best, parents = {start: 0}, {}
    queue = [(lower_bound(start, task) if heuristic else 0, 0, next(serial), start)]
    expanded, started = 0, time.monotonic()
    while queue:
        bound, cost, _, state = heapq.heappop(queue)
        if cost != best[state]:
            continue
        if complete(state, task):
            path = []
            while state != start:
                state, action = parents[state]
                path.append(action)
            path.reverse()
            check = replay(task, path)
            assert check['complete'] and check['cost'] == cost
            return dict(status='optimal_in_declared_model', cost=cost, actions=path,
                        manipulations=check['manipulations'], travel=check['travel'],
                        cut_sites=check['cut_sites'], expanded=expanded,
                        discovered=len(best), seconds=time.monotonic() - started)
        if expanded >= max_expanded or time.monotonic() - started >= timeout:
            return dict(status='budget_exhausted', expanded=expanded, frontier_lower_bound=bound,
                        seconds=time.monotonic() - started)
        expanded += 1
        for action, after, weight in transitions(state, task):
            if cut_sites is not None and action.startswith('cut:'):
                order = RAWS.index(NAMES.index(action.split(':')[1]))
                if cut_sites[order] is not None and state[0] != cut_sites[order]:
                    continue
            candidate = cost + weight
            if candidate < best.get(after, float('inf')):
                best[after] = candidate
                parents[after] = (state, action)
                h = lower_bound(after, task) if heuristic else 0
                heapq.heappush(queue, (candidate + h, candidate, next(serial), after))
    return dict(status='unreachable', expanded=expanded, seconds=time.monotonic() - started)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--tasks', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--timeout', type=float, default=90)
    p.add_argument('--max-expanded', type=int, default=500_000)
    p.add_argument('--comparators', action='store_true')
    p.add_argument('--uniform-crosscheck', action='store_true')
    a = p.parse_args()
    a.tasks = a.tasks.resolve()
    tasks = [Contract.from_dict(t) for t in json.loads(a.tasks.read_text())['tasks']]
    a.output.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[1]
    files = [Path(__file__), root / 'src/manipulation_agent/tasks/order_fulfillment.py',
             root / 'src/manipulation_agent/references/order_fulfillment_methods.py', a.tasks,
             root / 'task_designs/drafts/order_fulfillment_v1/skill/plan-coupled-orders/SKILL.md']
    frozen = a.output / 'source'; frozen.mkdir()
    for path in files:
        shutil.copy2(path, frozen / path.name)
    manifest = dict(created_utc=datetime.now(timezone.utc).isoformat(),
        scope='Exact finite-model task construction; no native simulation or model trials',
        source_sha256={str(p.relative_to(root)): digest(p) for p in files},
        oracle_exposed_to_policy=False, max_expanded=a.max_expanded, timeout=a.timeout,
        tasks=[asdict(t) for t in tasks], comparison_restrictions_apply_to_reference_only=True)
    (a.output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    rows = []
    public = a.output / 'public_cases'; public.mkdir()
    for task in tasks:
        case = public / task.name; case.mkdir()
        (case / 'task.json').write_text(json.dumps(task.public(), indent=2) + '\n')
        (case / 'task.txt').write_text(public_prompt(task))
        methods = {'free': None}
        if a.comparators:
            methods.update(source=(0, 0), destination=task.box_goal,
                           mixed_a_source=(0, task.box_goal[1]), mixed_b_source=(task.box_goal[0], 0))
        results = {}
        for label, sites in methods.items():
            results[label] = solve(task, sites, timeout=a.timeout, max_expanded=a.max_expanded)
            print(task.name, label, {k: results[label].get(k) for k in
                  ('status', 'cost', 'manipulations', 'travel', 'expanded', 'seconds')}, flush=True)
        if a.uniform_crosscheck:
            results['uniform_cost'] = solve(task, timeout=a.timeout, max_expanded=a.max_expanded, heuristic=False)
            if results['uniform_cost']['status'] == 'optimal_in_declared_model':
                assert results['uniform_cost']['cost'] == results['free']['cost']
        strategy = compile_methods(task)
        if strategy['selected'] is not None and 'cost' in results['free']:
            assert strategy['selected']['cost'] >= results['free']['cost']
        rows.append(dict(task=asdict(task), results=results, strategy=strategy))
        (a.output / f'{task.name}.json').write_text(json.dumps(rows[-1], indent=2) + '\n')
    (a.output / 'results.json').write_text(json.dumps(rows, indent=2) + '\n')


if __name__ == '__main__':
    main()
