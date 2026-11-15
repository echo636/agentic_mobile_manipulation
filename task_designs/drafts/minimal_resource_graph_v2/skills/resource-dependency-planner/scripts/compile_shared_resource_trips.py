"""Add three explicit resource-sharing choices to frozen processing methods.

No atomic-state search. Every extra pickup, load, trip and restoration is charged.
Each choice may be disabled; the all-disabled variant is the exact old method.
"""
from dataclasses import asdict
from itertools import product

import compile_mixed_resource_methods as previous


class SharedTripPlan(previous.base.Plan):
    def __init__(self, task, *, early_goals, paired_tool_return, fetch_box_with_tool):
        super().__init__(task)
        self.early_goals = early_goals
        self.paired_tool_return = paired_tool_return
        self.fetch_box_with_tool = fetch_box_with_tool

    def box_material_empty(self):
        return self.state[3] == 0 and self.state[6] == 0

    def carry_tool_and_box(self, destination, *, unload):
        if self.state[7] == self.state[0]:
            self.emit('pick_tool')
        if self.state[7] == 3:
            self.emit('put_tool_box')
        self.emit('pick_box')
        if self.state[0] != destination:
            self.emit('move')
        if unload:
            self.emit('pour_all')
        self.emit('put_box')

    def go(self, site):
        here = self.state[0]
        if here == site:
            return
        hand_empty = self.state[7] != 3 and self.state[8] != 2 and self.state[9] == 0
        if self.early_goals and hand_empty:
            tool = self.state[7] == here and self.task.tool_goal == site
            box = self.state[8] == here and self.task.box_goal == site and self.box_material_empty() and self.state[7] != 2
            if tool and box:
                self.carry_tool_and_box(site, unload=True)
                return
            if tool:
                self.emit('pick_tool'); self.emit('move'); self.emit('put_tool_surface')
                return
            if box:
                self.emit('pick_box'); self.emit('move'); self.emit('put_box')
                return
        super().go(site)

    def return_one(self, resource):
        here = self.state[0]
        if (self.paired_tool_return and resource == 'tool'
                and self.task.tool_goal != here and self.state[8] == here
                and self.box_material_empty() and self.state[9] == 0
                and self.state[7] in (here, 2, 3)):
            # Box may be useful at this intermediate station even if its final
            # destination differs; subsequent methods must still return it.
            self.carry_tool_and_box(self.task.tool_goal, unload=True)
            return
        super().return_one(resource)

    def box_at(self, site):
        if not self.fetch_box_with_tool or self.state[8] in (site, 2):
            return super().box_at(site)
        self.release()
        self.go(self.state[8])
        if self.box_material_empty() and self.state[7] in (self.state[0], 2):
            # Keep the tool inside while fetching the box. Later loading must
            # respect its unit of capacity; infeasible variants stay rejected.
            self.carry_tool_and_box(site, unload=False)
        else:
            self.emit('pick_box'); self.go(site); self.emit('put_box')


def compile_candidates(task):
    candidates = []
    for flags in product((False, True), repeat=3):
        options = dict(zip(('early_goals','paired_tool_return','fetch_box_with_tool'), flags))
        def factory(task):
            return SharedTripPlan(task, **options)
        compiled = previous.compile_candidates(task, plan_factory=factory)
        for candidate in compiled['candidates']:
            candidates.append(dict(candidate, method=dict(resource_sharing=options, original=candidate['method'])))
    successful = [i for i, candidate in enumerate(candidates) if candidate['success']]
    selected = min(successful, key=lambda i: candidates[i]['cost']) if successful else None
    baseline_count = len(candidates) // 8
    return dict(parameters=asdict(task), candidates=candidates, selected=selected,
                selected_cost=candidates[selected]['cost'] if selected is not None else None,
                baseline_cost=min((c['cost'] for c in candidates[:baseline_count] if c['success']), default=None),
                unique_action_sequences=len({tuple(c['actions']) for c in candidates}),
                oracle_used=False, method_variants=8)
