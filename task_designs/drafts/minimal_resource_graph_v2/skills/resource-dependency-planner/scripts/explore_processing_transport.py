#!/usr/bin/env python3
"""A finite processing/transport task screen, separate from RGB and physical evaluation."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import hashlib
import heapq
import json
from pathlib import Path
import shutil
import sys

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / 'src'))


@dataclass(frozen=True)
class Task:
    name: str
    count: int
    capacity: int
    travel_cost: int
    tool_start: int = 0
    box_start: int = 0
    tool_goal: int = 0
    box_goal: int = 0


DEV = [Task('D1_short_single', 1, 2, 1), Task('D2_long_single', 1, 2, 6),
       Task('D3_two_slots', 2, 2, 6), Task('D4_three_slots', 2, 3, 6)]
HELDOUT = [Task('H1_small_box', 1, 1, 4), Task('H2_medium_trip', 2, 2, 4),
           Task('H3_three_inputs', 3, 3, 5), Task('H4_tool_at_destination', 2, 2, 4, tool_start=1, tool_goal=1),
           Task('H5_box_finishes_at_destination', 2, 2, 5, box_goal=1),
           Task('H6_boundary', 1, 3, 2)]
ACTIONS = ['move', 'pick_raw_surface', 'pick_raw_box', 'pick_product_surface', 'pick_product_box',
           'put_raw_surface', 'put_raw_box', 'put_product_surface', 'put_product_box',
           'pick_tool', 'put_tool_surface', 'put_tool_box', 'pick_box', 'put_box', 'cut']


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def initial(task):
    # robot; raw counts left/right/box; product counts left/right/box;
    # tool left/right/box/held (0/1/2/3); box left/right/held (0/1/2); loose hand empty/raw/product (0/1/2)
    return (0, task.count, 0, 0, 0, 0, 0, task.tool_start, task.box_start, 0)


def invariant(state, task):
    loc, *rest = state
    raw, products = rest[:3], rest[3:6]
    tool, box, hand = rest[6:]
    assert loc in (0, 1) and all(n >= 0 for n in raw + products)
    assert 2 * sum(raw) + sum(products) + (2 if hand == 1 else 1 if hand == 2 else 0) == 2 * task.count
    assert (tool == 3) + (box == 2) + (hand != 0) <= 1
    assert raw[2] + products[2] + (tool == 2) <= task.capacity


def choices(state, task):
    loc = state[0]
    raw, products = list(state[1:4]), list(state[4:7])
    tool, box, hand = state[7:]
    result = {}

    def add(action, rr=None, pp=None, tt=tool, bb=box, hh=hand, ll=loc):
        after = (ll, *(raw if rr is None else rr), *(products if pp is None else pp), tt, bb, hh)
        invariant(after, task)
        result[action] = (after, task.travel_cost if action == 'move' else 1)

    add('move', ll=1 - loc)
    empty = hand == 0 and tool != 3 and box != 2
    if empty:
        for index, suffix in [(loc, 'surface')] + ([(2, 'box')] if box == loc else []):
            if raw[index]:
                updated = raw.copy(); updated[index] -= 1
                add('pick_raw_' + suffix, rr=updated, hh=1)
            if products[index]:
                updated = products.copy(); updated[index] -= 1
                add('pick_product_' + suffix, pp=updated, hh=2)
        if tool == loc or (tool == 2 and box == loc):
            add('pick_tool', tt=3)
        if box == loc:
            add('pick_box', bb=2)
    free_slot = box == loc and raw[2] + products[2] + (tool == 2) < task.capacity
    if hand:
        for index, suffix in [(loc, 'surface')] + ([(2, 'box')] if free_slot else []):
            if hand == 1:
                updated = raw.copy(); updated[index] += 1
                add('put_raw_' + suffix, rr=updated, hh=0)
            else:
                updated = products.copy(); updated[index] += 1
                add('put_product_' + suffix, pp=updated, hh=0)
    if tool == 3:
        add('put_tool_surface', tt=loc)
        if free_slot:
            add('put_tool_box', tt=2)
        if raw[loc]:
            rr, pp = raw.copy(), products.copy()
            rr[loc] -= 1; pp[loc] += 2
            add('cut', rr=rr, pp=pp)
    if box == 2:
        add('put_box', bb=loc)
    return result


def goal(state, task):
    return (sum(state[1:4]) == 0 and state[4:7] == (0, task.count * 2, 0)
            and state[7:] == (task.tool_goal, task.box_goal, 0))


def solve(task, cutting_site=None, allow_box=True):
    start = initial(task)
    best, parents = {start: 0}, {}
    queue = [(0, start)]
    while queue:
        cost, state = heapq.heappop(queue)
        if best[state] != cost:
            continue
        if goal(state, task):
            path = []
            while state != start:
                state, action = parents[state]
                path.append(action)
            return {'cost': cost, 'actions': path[::-1], 'states_discovered': len(best)}
        for action, (after, weight) in choices(state, task).items():
            if action == 'cut' and cutting_site is not None and state[0] != cutting_site:
                continue
            if not allow_box and (action.endswith('_box') or action in ('pick_box', 'put_box')):
                continue
            candidate = cost + weight
            if candidate < best.get(after, float('inf')):
                best[after] = candidate
                parents[after] = (state, action)
                heapq.heappush(queue, (candidate, after))
    return {'cost': None, 'actions': None, 'states_discovered': len(best)}


def replay(task, actions, optimum):
    state, cost, history = initial(task), 0, []
    if not isinstance(actions, list) or len(actions) > 160:
        return {'valid': False, 'success': False, 'cost': None, 'error': 'Expected at most 160 actions'}
    for i, action in enumerate(actions, 1):
        available = choices(state, task)
        if not isinstance(action, str) or action not in available:
            return {'valid': False, 'success': False, 'cost': None,
                    'error': {'at': i, 'action': action, 'state': state, 'legal': list(available)},
                    'history': history}
        before = state
        state, weight = available[action]
        cost += weight
        history.append({'step': i, 'action': action, 'before': before, 'after': state, 'cost': cost})
    success = goal(state, task)
    return {'valid': True, 'success': success, 'cost': cost if success else None,
            'executed_cost': cost, 'regret': cost - optimum if success else None,
            'actions': len(actions), 'crossings': actions.count('move'),
            'cut_sites': [h['before'][0] for h in history if h['action'] == 'cut'],
            'box_used': 'pick_box' in actions, 'history': history}


def prompt(task):
    label = ('left', 'right')
    return f'''Plan a discrete manipulation task using only the actions listed below.
There are two stations, left and right, each with an unlimited work surface. You start at left, empty-handed.
There are {task.count} identical raw blocks on the left surface and no products yet.
A cutting tool starts on the {label[task.tool_start]} surface. An empty open box starts on the {label[task.box_start]} surface.
The box has exactly {task.capacity} object slots. Each raw block, each product, and the tool uses ONE slot.
You can hold exactly one loose item OR the tool OR the box. A held box carries all its contents.
Raw blocks may be cut on the surface of EITHER station. One cut consumes one raw block and creates TWO products on that surface.
This is a discrete slot model, not an assertion that physical cutting doubles material volume.

Final requirements: all {task.count * 2} products on the RIGHT surface; tool on the {label[task.tool_goal]} surface;
empty box on the {label[task.box_goal]} surface; empty hand. The robot may finish at either station.
Each move between stations costs {task.travel_cost}. Every other action costs 1. Minimize total cost while meeting every requirement.

Actions, each a single string:
- move: cross to the other station; carries whatever is held. No other action moves the robot.
- pick_raw_surface / pick_product_surface: take one such item from the CURRENT station surface into the empty hand.
- pick_raw_box / pick_product_box: take one such item from the box into the empty hand; box must rest at CURRENT station.
- put_raw_surface / put_product_surface: put the matching currently held item on CURRENT station surface; frees the hand.
- put_raw_box / put_product_box: put the matching held item into the box; box must rest at CURRENT station and have a free slot; frees hand.
- pick_tool: take the tool from the CURRENT surface or from a box resting at CURRENT station; requires an empty hand.
- put_tool_surface: put the held tool onto CURRENT surface; frees hand.
- put_tool_box: put held tool into a box resting at CURRENT station with a free slot; frees hand.
- pick_box: take the box resting at CURRENT station; requires empty hand; contents remain inside.
- put_box: put the held box onto CURRENT surface; frees hand; contents remain inside.
- cut: requires holding the tool and a raw block on CURRENT surface; consumes it and creates two products there; tool remains held.

All items start as stated. No pouring, simultaneous holding, implicit loading/unloading, or cutting inside the box is available.
Actions are deterministic and always succeed when their stated preconditions hold.
Return JSON with actions (the full ordered string list) and notes (a brief public planning note).
No tools or code execution are available. Do not output model-invented macro actions.
'''


def prepare(output, pilot, independent):
    output.mkdir(parents=True, exist_ok=False)
    shutil.copy2(__file__, output / Path(__file__).name)
    common_files = sorted((pilot / 'model_source_A_v1/skills').glob('*/SKILL.md'))
    common = ('This is an offline symbolic planning diagnostic, not a live simulator. '
              'The task supplies complete state and action semantics instead of RGB. '
              'Apply relevant shared guidance subject to the task semantics. No tools are callable; '
              'read_skill and task_notes refer to supplied policy text and the brief notes field.\n\n')
    common += '\n\n'.join(f'Policy: {p.parent.name}\n{p.read_text()}' for p in common_files)
    (output / 'common_policy.txt').write_text(common)
    shutil.copy2(pilot / 'candidate_policy_v2/goal-decomposition/SKILL.md', output / 'policy_B.md')
    checks = []
    independent_rows = json.loads(independent.read_text())
    for row in independent_rows:
        task = Task('crosscheck', row['n'], row['capacity'], row['distance'])
        for key, site in [('unrestricted', None), ('source_cut_only', 0), ('destination_cut_only', 1)]:
            result = solve(task, site)
            assert result['cost'] == row[key]['cost'], (asdict(task), key)
            assert replay(task, result['actions'], result['cost'])['success']
        checks.append({'count': task.count, 'capacity': task.capacity, 'travel_cost': task.travel_cost,
                       'all_three_optima_agree': True})
    write(output / 'independent_checks.json', checks)
    manifest = {'created_at': datetime.now(timezone.utc).isoformat(),
                'scope': 'Development and held-out symbolic instances in one processing/transport family',
                'development': [asdict(t) for t in DEV], 'heldout': [asdict(t) for t in HELDOUT],
                'development_arms': ['A', 'B'], 'heldout_arms': ['A', 'B', 'C'],
                'repeats_per_instance_arm': 1, 'model': 'gpt-6.1-sol', 'effort': 'low',
                'provider': 'OpenAI', 'policy_retries': 0, 'timeout_seconds': 180,
                'stop_rule': 'Complete the fixed cases; preserve all answers; no heldout-driven revision.',
                'A': 'Common operating policies only', 'B': 'Common plus original frozen planning skill',
                'C': 'One revision after development answers; frozen before any heldout model call',
                'common_policy_sha256': sha(output / 'common_policy.txt'),
                'B_sha256': sha(output / 'policy_B.md'),
                'independent_reference_sha256': sha(independent), 'no_physics_claim': True}
    write(output / 'design.json', manifest)
    cases = output / 'cases'; cases.mkdir()
    for task in DEV + HELDOUT:
        p = cases / task.name; p.mkdir()
        write(p / 'task.json', asdict(task)); (p / 'prompt.txt').write_text(prompt(task))
        values = {name: solve(task, site, box) for name, site, box in
                  [('optimal', None, True), ('source_only', 0, True), ('destination_only', 1, True),
                   ('no_box', None, False)]}
        for value in values.values():
            if value['cost'] is not None:
                assert replay(task, value['actions'], value['cost'])['success']
        write(p / 'reference.json', values)
        print(task.name, {k: v['cost'] for k, v in values.items()}, flush=True)
    schema = {'type': 'object', 'properties': {
        'actions': {'type': 'array', 'items': {'type': 'string', 'enum': ACTIONS}},
        'notes': {'type': 'string'}}, 'required': ['actions', 'notes'], 'additionalProperties': False}
    write(output / 'response_schema.json', schema)
    # Negative cases check semantic failures, not formatting alone.
    task = DEV[0]
    assert not replay(task, ['cut'], 9)['valid']
    assert not replay(task, ['pick_tool', 'pick_box'], 9)['valid']
    assert not replay(task, ['pick_raw_surface', 'put_raw_box', 'pick_tool', 'cut'], 9)['valid']
    assert not replay(task, [], 9)['success']


def run(output, phase, candidate):
    from manipulation_agent.clients import ClientConfig, get_adapter

    design = json.loads((output / 'design.json').read_text())
    root = output / phase; root.mkdir(exist_ok=False)
    if phase == 'heldout':
        if candidate is None:
            raise ValueError('Heldout requires frozen candidate C')
        shutil.copy2(candidate, root / 'policy_C.md')
    tasks = [Task(**t) for t in design[phase]]
    arms = ['A', 'B'] if phase == 'development' else ['A', 'B', 'C']
    schedule = [(task.name, arm) for i, task in enumerate(tasks)
                for arm in arms[i % len(arms):] + arms[:i % len(arms)]]
    write(root / 'preregistration.json', {'created_at': datetime.now(timezone.utc).isoformat(),
          'schedule': schedule, 'design_sha256': sha(output / 'design.json'),
          'candidate_C_sha256': sha(root / 'policy_C.md') if phase == 'heldout' else None,
          'source_sha256': sha(Path(__file__)),
          'task_prompt_sha256': {t.name: sha(output / 'cases' / t.name / 'prompt.txt') for t in tasks}})
    adapter = get_adapter('codex')
    capability = adapter.probe()
    write(root / 'client.json', asdict(capability))
    if capability.status != 'available':
        raise RuntimeError('Model client unavailable')
    rows = []
    for name, arm in schedule:
        task = next(t for t in tasks if t.name == name)
        folder = root / (name + '_' + arm); folder.mkdir()
        policy = (output / 'common_policy.txt').read_text()
        if arm != 'A':
            policy += '\n\nAdditional planning guidance:\n' + (
                output / 'policy_B.md' if arm == 'B' else root / 'policy_C.md').read_text()
        task_text = (output / 'cases' / name / 'prompt.txt').read_text()
        optimum = json.loads((output / 'cases' / name / 'reference.json').read_text())['optimal']['cost']
        config = ClientConfig('codex', design['model'], task_text, sys.executable, [], folder,
                              design['timeout_seconds'], 'skills', False, design['effort'], design['provider'])
        project = adapter.prepare_project(config, policy, [])
        argv, index = [], 0
        while index < len(project.argv):
            if project.argv[index] == '-c' and project.argv[index + 1].startswith('mcp_servers.manipulation.'):
                index += 2
            else:
                argv.append(project.argv[index]); index += 1
        assert argv[-1] == '-'
        argv[-1:] = ['--output-schema', str(output / 'response_schema.json'),
                     '--output-last-message', str(folder / 'answer.json'), '-']
        project = replace(project, argv=argv, stdin=task_text)
        (folder / 'policy.txt').write_text(policy)
        result = adapter.run(project, folder, design['timeout_seconds'])
        row = {'case': name, 'arm': arm, 'client': asdict(result), 'optimum': optimum,
               'usable_response': result.returncode == 0 and not result.timed_out}
        try:
            answer = json.loads((folder / 'answer.json').read_text())
            row['evaluation'] = replay(task, answer['actions'], optimum)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            row['evaluation'] = {'valid': False, 'success': False, 'cost': None, 'error': str(exc)}
        write(folder / 'result.json', row); rows.append(row)
        write(root / 'results.json', rows)
        print(json.dumps({'case': name, 'arm': arm, 'optimal': optimum,
              'success': row['evaluation']['success'], 'cost': row['evaluation'].get('cost'),
              'error': row['evaluation'].get('error')}, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('phase', choices=['prepare', 'development', 'heldout'])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--pilot', type=Path)
    parser.add_argument('--independent', type=Path)
    parser.add_argument('--candidate', type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if args.phase == 'prepare':
        if args.pilot is None or args.independent is None:
            parser.error('prepare requires --pilot and --independent')
        prepare(output, args.pilot.resolve(), args.independent.resolve())
    else:
        run(output, args.phase, args.candidate)


if __name__ == '__main__':
    main()
