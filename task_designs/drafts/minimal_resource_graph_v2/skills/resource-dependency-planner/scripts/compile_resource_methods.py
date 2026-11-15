#!/usr/bin/env python3
"""Finite, explicit processing/transport methods; no atomic-state search.

This is an offline planning component, not a text skill or robot controller.
Every emitted action is checked against the public conserved-volume model.
"""
from dataclasses import asdict
from itertools import product

import explore_processing_transport as core
from explore_conserved_capacity import choices


class Unsupported(ValueError):
    pass


class Plan:
    def __init__(self, task):
        self.task = task
        self.state = core.initial(task)
        self.actions = []
        self.cost = 0

    def emit(self, action):
        available = choices(self.state, self.task)
        if action not in available:
            raise Unsupported(f'{action} unavailable in {self.state}')
        self.state, cost = available[action]
        self.cost += cost
        self.actions.append(action)

    def go(self, site):
        if self.state[0] != site:
            self.emit('move')

    def release(self):
        if self.state[7] == 3:
            self.emit('put_tool_surface')
        elif self.state[8] == 2:
            self.emit('put_box')
        elif self.state[9]:
            self.emit('put_raw_surface' if self.state[9] == 1 else 'put_product_surface')

    def get_tool(self):
        if self.state[7] == 3:
            return
        self.release()
        site = self.state[8] if self.state[7] == 2 else self.state[7]
        self.go(site)
        self.emit('pick_tool')

    def box_at(self, site):
        if self.state[8] == site:
            self.go(site)
            return
        if self.state[8] != 2:
            self.release()
            self.go(self.state[8])
            self.emit('pick_box')
        self.go(site)
        self.emit('put_box')

    def direct(self, kind, source, destination, count):
        self.release()
        for _ in range(count):
            self.go(source)
            self.emit('pick_' + kind + '_surface')
            self.go(destination)
            self.emit('put_' + kind + '_surface')

    def load(self, kind, count):
        for _ in range(count):
            self.emit('pick_' + kind + '_surface')
            self.emit('put_' + kind + '_box')

    def cut(self, site, count, inside=False):
        self.get_tool()
        self.go(site)
        for _ in range(count):
            self.emit('cut_in_box' if inside else 'cut')

    def ship(self, destination):
        self.emit('pick_box')
        self.go(destination)
        self.emit('pour_all')

    def return_one(self, resource):
        index = 7 if resource == 'tool' else 8
        goal = self.task.tool_goal if resource == 'tool' else self.task.box_goal
        held = 3 if resource == 'tool' else 2
        if self.state[index] == goal:
            return
        if self.state[index] != held:
            self.release()
            if resource == 'tool':
                self.get_tool()
            else:
                self.go(self.state[8])
                self.emit('pick_box')
        self.go(goal)
        self.emit('put_tool_surface' if resource == 'tool' else 'put_box')

    def finish(self, joint):
        # This declared method shares a return only when both obligations have
        # the same endpoint and the tool is already at the box's location.
        tool_site = self.state[0] if self.state[7] == 3 else self.state[7]
        box_site = self.state[0] if self.state[8] == 2 else self.state[8]
        if (joint and tool_site == box_site and tool_site != self.task.tool_goal
                and self.task.tool_goal == self.task.box_goal):
            if self.state[8] == 2:
                self.emit('put_box')
            self.go(tool_site)
            if self.state[7] != 3:
                self.emit('pick_tool')
            self.emit('put_tool_box')
            self.ship(self.task.box_goal)
            self.emit('put_box')
        else:
            # Preserve a currently held resource instead of putting it down
            # only to pick it up for its return.
            order = ('box', 'tool') if self.state[8] == 2 else ('tool', 'box')
            for resource in order:
                self.return_one(resource)
        self.release()


def compile_one(task, cut_site, transport, joint, *, plan_factory=Plan):
    plan = plan_factory(task)
    try:
        if transport == 'direct':
            if cut_site == 1:
                plan.direct('raw', 0, 1, task.count)
            plan.cut(cut_site, task.count)
            if cut_site == 0:
                # Retain the tool for a later shared trip only in carrier
                # methods. Direct transport has a separate resource return.
                plan.release()
                plan.direct('product', 0, 1, 2 * task.count)
        elif transport == 'carrier_surface_cut':
            if cut_site == 0:
                plan.cut(0, task.count)
                plan.release()
                kind, remaining, batch_size = 'product', 2 * task.count, task.capacity
            else:
                kind, remaining, batch_size = 'raw', task.count, task.capacity // 2
            if batch_size < 1:
                raise Unsupported('carrier cannot fit one item of the selected material')
            while remaining:
                plan.box_at(0)
                batch = min(remaining, batch_size)
                plan.load(kind, batch)
                remaining -= batch
                # Optional co-transport is declared by the joint method; no
                # search inserts an unanticipated resource operation.
                material_units = batch * (2 if kind == 'raw' else 1)
                if (joint and not remaining and plan.state[7] == 0
                        and (cut_site == 1 or task.tool_goal == 1)
                        and material_units + 1 <= task.capacity):
                    plan.get_tool()
                    plan.emit('put_tool_box')
                plan.ship(1)
                if remaining:
                    plan.go(0)
                    plan.emit('put_box')
            if cut_site == 1:
                plan.release()
                plan.cut(1, task.count)
        elif transport == 'carrier_inside_cut':
            batch_size = task.capacity // 2
            if batch_size < 1:
                raise Unsupported('carrier cannot fit one raw item')
            remaining = task.count
            while remaining:
                plan.box_at(0)
                batch = min(remaining, batch_size)
                plan.load('raw', batch)
                remaining -= batch
                if cut_site == 1:
                    if (joint and plan.state[7] == 0 and 2 * batch + 1 <= task.capacity):
                        plan.get_tool()
                        plan.emit('put_tool_box')
                    plan.emit('pick_box')
                    plan.go(1)
                    plan.emit('put_box')
                plan.cut(cut_site, batch, inside=True)
                if (joint and not remaining and cut_site == 0 and task.tool_goal == 1
                        and 2 * batch + 1 <= task.capacity):
                    plan.emit('put_tool_box')
                else:
                    plan.release()
                plan.ship(1)
                if remaining:
                    plan.go(0)
                    plan.emit('put_box')
        else:
            raise ValueError(transport)
        plan.finish(joint)
        success = core.goal(plan.state, task)
        return {'supported': True, 'success': success, 'cost': plan.cost if success else None,
                'actions': plan.actions, 'final_state': plan.state,
                'error': None if success else 'compiled plan did not meet all final obligations'}
    except Unsupported as error:
        return {'supported': False, 'success': False, 'cost': None,
                'actions': plan.actions, 'final_state': plan.state, 'error': str(error)}


def compile_candidates(task, *, plan_factory=Plan):
    candidates = []
    for site, transport, joint in product(
            (0, 1), ('direct', 'carrier_surface_cut', 'carrier_inside_cut'), (False, True)):
        result = compile_one(task, site, transport, joint, plan_factory=plan_factory)
        result['method'] = {'cut_site': site, 'transport': transport,
                            'joint_resource_return': joint}
        candidates.append(result)
    successful = [i for i, result in enumerate(candidates) if result['success']]
    selected = min(successful, key=lambda i: candidates[i]['cost']) if successful else None
    return {'parameters': asdict(task), 'candidates': candidates, 'selected': selected,
            'selected_cost': candidates[selected]['cost'] if selected is not None else None,
            'unique_action_sequences': len({tuple(row['actions']) for row in candidates}),
            'oracle_used': False}
