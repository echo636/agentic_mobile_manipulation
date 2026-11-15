"""Expand finite material splits and shared trips, without atomic-state search."""
from dataclasses import asdict
from itertools import product

import compile_resource_methods as base


def mixed(task, raw_right, raw_inside, extra_products, joint, right_early, return_early, *, plan_factory=base.Plan):
    p = plan_factory(task)
    try:
        if raw_right:
            p.direct('raw', 0, 1, raw_right)
            if right_early:
                p.cut(1, raw_right)
                if task.tool_goal == 0:
                    p.return_one('tool')
                else:
                    p.release()
        payload = 2 * raw_inside + extra_products
        if payload:
            # After the final outbound raw delivery, the robot is already at
            # a remote empty box. Its return trip can bring that box to source.
            p.box_at(0)
            p.load('raw', raw_inside)
        surface_count = task.count - raw_right - raw_inside
        if surface_count:
            p.cut(0, surface_count)
        if raw_inside:
            p.cut(0, raw_inside, inside=True)
        if payload:
            p.release()
            p.go(0)
            p.load('product', extra_products)
            if (joint and p.state[7] == 0 and payload + 1 <= task.capacity
                    and (task.tool_goal == 1 or (raw_right and not right_early))):
                p.get_tool()
                p.emit('put_tool_box')
            p.ship(1)
        if raw_right and not right_early:
            p.cut(1, raw_right)
        if return_early:
            p.finish(joint)
        residual = p.state[4]
        if residual:
            p.direct('product', 0, 1, residual)
        p.finish(joint)
        success = base.core.goal(p.state, task)
        return dict(supported=True, success=success, actions=p.actions,
                    cost=p.cost if success else None, final_state=p.state,
                    error=None if success else 'incomplete final obligations')
    except base.Unsupported as exc:
        return dict(supported=False, success=False, actions=p.actions,
                    cost=None, final_state=p.state, error=str(exc))


def compile_candidates(task, *, plan_factory=base.Plan):
    candidates = base.compile_candidates(task, plan_factory=plan_factory)['candidates']
    for c in candidates:
        c['method'] = dict(family='v1', **c['method'])
    for raw_right in range(task.count + 1):
        remaining = task.count - raw_right
        for raw_inside in range(min(remaining, task.capacity // 2) + 1):
            maximum = min(2 * (remaining - raw_inside), task.capacity - 2 * raw_inside)
            for extra in range(maximum + 1):
                for joint, early, returns in product((False, True), repeat=3):
                    if not raw_right and early:
                        continue
                    row = mixed(task, raw_right, raw_inside, extra, joint, early, returns, plan_factory=plan_factory)
                    row['method'] = dict(family='mixed', raw_right=raw_right,
                        raw_inside=raw_inside, extra_products=extra, joint=joint,
                        right_early=early, return_early=returns)
                    candidates.append(row)
    successful = [i for i, c in enumerate(candidates) if c['success']]
    selected = min(successful, key=lambda i: candidates[i]['cost']) if successful else None
    return dict(parameters=asdict(task), candidates=candidates, selected=selected,
        selected_cost=candidates[selected]['cost'] if selected is not None else None,
        unique_action_sequences=len({tuple(c['actions']) for c in candidates}), oracle_used=False,
        baseline_cost=min((c['cost'] for c in candidates[:12] if c['success']), default=None))
