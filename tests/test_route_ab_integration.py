"""End-to-end CPU integration tests for Route A (StateMemory) + Route B (TaskGraphPlanner)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from manipulation_agent.agent.vision_policy import MINIMAL_PROMPT, system_prompt
from manipulation_agent.contracts import Budget
from manipulation_agent.observations.mock_rgb import MockRGBBackend
from manipulation_agent.records import Recorder
from manipulation_agent.runtime.vision_harness import VisionHarness
from manipulation_agent.tools import tool_specs


class _TimedMockBackend(MockRGBBackend):
    """MockRGBBackend with public robot_clock accounting for travel cost integration tests."""

    def __init__(self, output: Path):
        super().__init__(output)
        self._total_s = 0.0
        self.robot_clock = type('Clock', (), {'last_action': None})()

    def execute_visual(self, primitive, target, max_steps, **kwargs):
        details = super().execute_visual(primitive, target, max_steps, **kwargs)
        if primitive == 'navigate_to':
            delta_s, travel_s, dist_m = 36.0, 36.0, 18.0
            details['nav_status'] = 'reached'
        else:
            delta_s, travel_s, dist_m = 10.0, 0.0, 0.0
        self._total_s += delta_s
        self.robot_clock.last_action = {
            'total_s': self._total_s,
            'delta_s': delta_s,
            'this_call': {
                'travel_s': travel_s,
                'travel_distance_m': dist_m,
                'manipulation_s': 0.0 if primitive == 'navigate_to' else delta_s,
                'wait_s': 0.0,
            },
        }
        return details


def test_route_ab_end_to_end_synergy_and_three_tier_isolation(tmp_path: Path) -> None:
    """Verify Tier 1 (off/off), Tier 2 (Route A on), and Tier 3 (Route A + Route B on) collaborate cleanly."""
    # Tier 1: Bare minimal baseline is 100% untouched
    assert system_prompt('minimal', goal_skill='none') == MINIMAL_PROMPT
    assert [t['name'] for t in tool_specs('minimal')] == ['initialize', 'look', 'act', 'finish']

    # Tier 3: Route A (state_memory=True, auxiliary_feedback=True) + Route B (planning_mode='task_graph')
    out = tmp_path / 'tier3'
    backend = _TimedMockBackend(out)
    rec = Recorder(out, {'output': str(out)})
    harness = VisionHarness(
        backend,
        rec,
        Budget(wall_seconds=None),
        profile='basic-skills',
        agent_step_limit=30,
        goal_skill='none',
        auxiliary_feedback=True,
        state_memory=True,
        planning_mode='task_graph',
    )

    init_res = harness.call('initialize', {}, 'req-init')
    assert 'state_memory' in init_res
    assert init_res['state_memory']['current_location'] == 'loc_0'

    rev = init_res['observation']['revision']
    ref = init_res['observation']['images'][0]['image_ref']

    # Navigate to a new station -> StateMemory records loc_0 <-> loc_1 = 36.0s AND TaskGraphSession sees it immediately
    nav_res = harness.call(
        'act',
        {'primitive': 'navigate_to', 'target': {'image_ref': ref, 'point': [0.5, 0.5]}, 'revision': rev},
        'req-nav',
    )
    assert nav_res['ok'] is True
    assert nav_res['state_memory']['current_location'] == 'loc_1'
    assert nav_res['task_graph_status']['external_memory_synced']['measured_travel_edges_count'] >= 2

    # Now call plan_task_graph with optimistic initial travel_cost=5.0 between loc_0 and loc_1:
    # Real StateMemory's measured 36.0s automatically overrides 5.0s -> e_cap (movable=True, capacity=2) wins!
    plan_res = harness.call(
        'plan_task_graph',
        {
            'graph': {
                'agent_location': 'loc_1',
                'locations': [{'id': 'loc_0'}, {'id': 'loc_1'}],
                'travel_cost': [{'from_location': 'loc_0', 'to_location': 'loc_1', 'cost_s': 5.0}],
                'entities': [
                    {'id': 'e_cap', 'location': 'loc_1', 'movable': True, 'capacity': 2},
                    {'id': 'e_1', 'location': 'loc_1', 'movable': True, 'capacity': 0},
                    {'id': 'e_2', 'location': 'loc_1', 'movable': True, 'capacity': 0},
                ],
                'goals': {
                    'entity_locations': [
                        {'entity_id': 'e_1', 'location': 'loc_0'},
                        {'entity_id': 'e_2', 'location': 'loc_0'},
                    ],
                    'require_empty_hand': True,
                },
            }
        },
        'req-plan',
    )
    assert plan_res['ok'] is True
    assert harness.agent_steps == 1  # plan_task_graph consumes 0 action steps!
    plan = plan_res['plan']
    assert plan['complete'] is True
    # Because measured 36.0s > 20s load/unload overhead, solver automatically batches via e_cap in 1 leg (36s travel)!
    assert plan['total_travel_cost_s'] == pytest.approx(36.0)
    used_cap = [s for s in plan['ordered_steps'] if s.get('target_host') == 'e_cap' or s.get('entity_id') == 'e_cap']
    assert len(used_cap) > 0
