"""Pure abstract unit tests for Route B: General Precondition & Cost-Aware Task Graph Planner.

Uses strictly abstract symbolic identifiers (L1, L2, L3, L_buf, var_x, e_1, op_1)
to verify:
1. Zero domain-specific vocabulary and zero simulator imports across all Route B
   modules, tool schemas, prompts, and skill instructions.
2. Default-off (`--planning-mode off`) byte-for-byte preservation of baseline
   prompts, tool catalogs, and skill manifests across all profiles and CLIs.
3. Strict MCP transport closed-object schema compatibility (`additionalProperties: False`).
4. Unified zero-special-case search solver naturally emerging all 5 optimal behaviors:
   (a) Asynchronous parallel overlap of `passive_duration_s > 0` evolutions;
   (b) Multi-item batching & cyclic return consolidation (`L1->L2->L3->L1`) via
       `movable=True, capacity>=2` entities (plus automatic switch to direct
       single-hand transfer when `travel_cost` < load/unload overhead);
   (c) Minimum-reset monotonic state ordering under threshold preconditions and
       spatial reset cost;
   (d) Deadlock-free bounded-buffer swapping (`L_buf` with `capacity=K`) when
       full locations require empty-state preprocessing before placement;
   (e) Value-of-Information observation scheduling (`resolves_unknowns`) before
       costly remote transfers when blind hauling risks return-trip penalties.
5. Lightweight incremental Delta Merge, public `robot_time` travel cost fusion,
   and duck-typed zero-config ingestion from Route A's `state_memory`.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from manipulation_agent.agent.task_graph_planner import TaskGraphSession, solve_task_graph
from manipulation_agent.agent.vision_policy import (
    BASIC_SKILLS_PROMPT,
    MINIMAL_PROMPT,
    TASK_GRAPH_INLINE_INSTRUCTION,
    TASK_GRAPH_PLANNING_INSTRUCTION,
    system_prompt,
)
from manipulation_agent.contracts import Budget, validate
from manipulation_agent.mcp_server import create_server
from manipulation_agent.observations.mock_rgb import MockRGBBackend
from manipulation_agent.records import Recorder
from manipulation_agent.runtime.vision_harness import VisionHarness
from manipulation_agent.tools import tool_specs

REPO_ROOT = Path(__file__).resolve().parents[1]


def _forbidden_domain_patterns() -> list[str]:
    tokens = [
        'timed_' + 'processes',
        'timed_' + 'process',
        'car' + 'riers',
        'car' + 'rier',
        'appli' + 'ance',
        'appli' + 'ances',
        'micro' + 'wave',
        'wash' + 'er',
        'dry' + 'er',
        'ket' + 'tle',
        'tr' + 'ay',
        'bas' + 'ket',
        'r' + 'ag',
        'kni' + 'fe',
        'so' + 'ak',
        'clean_to_' + 'dirty',
        'refrig' + 'erator',
        'laun' + 'dry',
        'ki' + 'ln',
        'hot' + 'dog',
        'kit' + 'chen',
        'bed' + 'room',
        'pat' + 'io',
    ]
    return [rf'\b{tok}\b' for tok in tokens]


def test_zero_domain_words_and_zero_simulator_imports() -> None:
    """Verify Route B code, schema, prompt instructions, and skill contain zero domain terms."""
    paths = [
        REPO_ROOT / 'src/manipulation_agent/agent/task_graph_planner.py',
        REPO_ROOT / 'src/manipulation_agent/tools/task_graph.py',
        REPO_ROOT / 'skills/task-graph-planning/SKILL.md',
    ]
    pattern = re.compile('|'.join(_forbidden_domain_patterns()), re.IGNORECASE)
    for path in paths:
        text = path.read_text(encoding='utf-8')
        match = pattern.search(text)
        assert match is None, f'Forbidden domain token {match.group(0)!r} found in {path}'
        if path.suffix == '.py':
            lowered = text.lower()
            assert 'omnigibson' not in lowered
            assert 'pxr' not in lowered
            assert 'bddl' not in lowered

    for prompt_snippet in (TASK_GRAPH_PLANNING_INSTRUCTION, TASK_GRAPH_INLINE_INSTRUCTION):
        match = pattern.search(prompt_snippet)
        assert match is None, f'Forbidden domain token {match.group(0)!r} in prompt instruction'

    specs = tool_specs('basic-skills', planning_mode='task_graph')
    tg_spec = next(s for s in specs if s['name'] == 'plan_task_graph')
    serialized_schema = json.dumps(tg_spec)
    match = pattern.search(serialized_schema)
    assert match is None, f'Forbidden domain token {match.group(0)!r} in plan_task_graph schema'


def test_default_off_switch_preserves_baseline_across_profiles_and_clis(tmp_path: Path) -> None:
    """Verify `--planning-mode off` (default) leaves prompts, tools, and skill digests untouched."""
    assert system_prompt('minimal', goal_skill='none') == MINIMAL_PROMPT
    assert system_prompt('basic-skills', goal_skill='none') == BASIC_SKILLS_PROMPT

    for profile in ('minimal', 'basic-skills', 'goal-tree', 'skills', 'skills_inline', 'workflow'):
        names_off = [s['name'] for s in tool_specs(profile)]
        assert 'plan_task_graph' not in names_off
        names_on = [s['name'] for s in tool_specs(profile, planning_mode='task_graph')]
        assert 'plan_task_graph' in names_on

    out = tmp_path / 'run_off'
    backend = MockRGBBackend(out)
    rec = Recorder(out, {'output': str(out)})
    harness = VisionHarness(
        backend, rec, Budget(wall_seconds=None), profile='basic-skills', agent_step_limit=15, goal_skill='none'
    )
    assert harness.planning_mode == 'off'
    assert 'plan_task_graph' not in harness.catalog
    skill_names = {entry['name'] for entry in harness.skills.catalog()}
    assert 'task-graph-planning' not in skill_names

    init_res = harness.call('initialize', {}, 'req-init')
    rev = init_res['observation']['revision']
    ref = init_res['observation']['images'][0]['image_ref']
    act_res = harness.call(
        'act',
        {'primitive': 'toggle_on', 'target': {'image_ref': ref, 'point': [0.5, 0.5]}, 'revision': rev},
        'req-act',
    )
    assert act_res['ok'] is True
    assert 'task_graph_status' not in act_res

    # Verify CLI --planning-mode {off,task_graph} flags exist and default to off
    env = {'PYTHONPATH': str(REPO_ROOT / 'src')}
    for cmd in (
        [sys.executable, '-m', 'manipulation_agent.vision_cli', '--help'],
        [sys.executable, '-m', 'manipulation_agent.mcp_server', '--help'],
        [sys.executable, str(REPO_ROOT / 'scripts/run_codex_controller.py'), '--help'],
    ):
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True, check=True, timeout=15)
        assert '--planning-mode {off,task_graph}' in proc.stdout


def test_strict_mcp_transport_schema_compatibility() -> None:
    """Verify `plan_task_graph` uses closed object schemas compatible with strict MCP wire transport."""
    spec_loader = importlib.util.spec_from_file_location(
        'strict_transport', REPO_ROOT / 'scripts/transport/trace_responses_transport.py'
    )
    assert spec_loader is not None and spec_loader.loader is not None
    module = importlib.util.module_from_spec(spec_loader)
    spec_loader.loader.exec_module(module)

    functions = [
        {
            'type': 'function',
            'name': row['name'],
            'strict': False,
            'description': row['description'],
            'parameters': row['inputSchema'],
        }
        for row in tool_specs('skills', planning_mode='task_graph')
    ]
    payload = {
        'model': 'test-model',
        'tools': [{'type': 'namespace', 'name': 'mcp__manipulation', 'tools': functions}],
    }
    strict_payload = module.strict_mcp_request(payload)
    tg_tool = next(t for t in strict_payload['tools'][0]['tools'] if t['name'] == 'plan_task_graph')
    assert tg_tool['strict'] is True

    orig_tg = next(
        row['inputSchema'] for row in tool_specs('skills', planning_mode='task_graph') if row['name'] == 'plan_task_graph'
    )
    sample_args = {
        'graph': {
            'agent_location': 'L1',
            'locations': [{'id': 'L1'}, {'id': 'L2', 'capacity': 2}],
            'travel_cost': [{'from_location': 'L1', 'to_location': 'L2', 'cost_s': 25.0}],
            'state_vars': [{'name': 'var_x', 'value': 0}],
            'entities': [{'id': 'e_1', 'location': 'L1', 'movable': True, 'capacity': 0}],
            'operations': [{'id': 'op_1', 'location': 'L1', 'effects': [{'var': 'var_x', 'value': 1}]}],
            'goals': {
                'predicates': [{'var': 'var_x', 'value': 1}],
                'entity_locations': [{'entity_id': 'e_1', 'location': 'L2'}],
            },
        },
        'completed_operations': [],
    }
    validate(sample_args, orig_tg)


# ---------------------------------------------------------------------------
# 5 Emergent Optimal Planning Behaviors on Pure Abstract Symbols
# ---------------------------------------------------------------------------


def test_emergence_a_asynchronous_passive_parallelism() -> None:
    """(a) Unattended `passive_duration_s > 0` evolutions trigger early and pack independent ops in window."""
    spec = {
        'agent_location': 'L2',
        'locations': [{'id': 'L1'}, {'id': 'L2'}],
        'travel_cost': [{'from_location': 'L1', 'to_location': 'L2', 'cost_s': 15.0}],
        'state_vars': [
            {'name': 'var_p', 'value': 0},
            {'name': 'var_q', 'value': 0},
            {'name': 'var_r', 'value': 0},
            {'name': 'var_out', 'value': 0},
        ],
        'operations': [
            # Even though agent starts at L2 where op_2 and op_3 are 0m away, going to L1 first to trigger op_1 saves 120s!
            {
                'id': 'op_1',
                'location': 'L1',
                'active_duration_s': 20.0,
                'occupies_hand': False,
                'passive_duration_s': 150.0,
                'effects': [{'var': 'var_p', 'value': 1}],
            },
            {
                'id': 'op_2',
                'location': 'L2',
                'active_duration_s': 60.0,
                'occupies_hand': True,
                'effects': [{'var': 'var_q', 'value': 1}],
            },
            {
                'id': 'op_3',
                'location': 'L2',
                'active_duration_s': 60.0,
                'occupies_hand': True,
                'effects': [{'var': 'var_r', 'value': 1}],
            },
            {
                'id': 'op_4',
                'location': 'L1',
                'preconditions': [{'var': 'var_p', 'op': 'eq', 'value': 1}],
                'active_duration_s': 15.0,
                'occupies_hand': True,
                'effects': [{'var': 'var_out', 'value': 1}],
            },
        ],
        'goals': {
            'predicates': [
                {'var': 'var_q', 'value': 1},
                {'var': 'var_r', 'value': 1},
                {'var': 'var_out', 'value': 1},
            ],
        },
    }
    plan = solve_task_graph(spec)
    assert plan['complete'] is True
    assert plan['status'] == 'optimal'

    op_order = [step['op_id'] for step in plan['ordered_steps'] if step['kind'] == 'operation']
    assert op_order[0] == 'op_1'
    assert set(op_order[1:3]) == {'op_2', 'op_3'}
    assert op_order[3] == 'op_4'

    # L2->L1 (15) + op_1 (20) => t=35, passive ready at 185.
    # L1->L2 (15) + op_2 (60) + op_3 (60) + L2->L1 (15) => arrives L1 at t=185 (0s idle wait!) + op_4 (15) = 200s!
    # Naive local-first order would take 60 + 60 + 15 + 20 + 150 + 15 = 320s.
    assert plan['total_idle_wait_s'] == pytest.approx(0.0)
    assert plan['estimated_finish_clock_s'] == pytest.approx(200.0)

    # Also verify 2-stage chained passive evolutions (op_s1 -> op_s2) interleaved with independent work
    chained_spec = {
        'agent_location': 'L1',
        'locations': [{'id': 'L1'}, {'id': 'L2'}],
        'travel_cost': [{'from_location': 'L1', 'to_location': 'L2', 'cost_s': 10.0}],
        'state_vars': [{'name': 'v_s1', 'value': 0}, {'name': 'v_s2', 'value': 0}, {'name': 'v_ind', 'value': 0}],
        'operations': [
            {'id': 'op_s1', 'location': 'L1', 'active_duration_s': 15.0, 'occupies_hand': False, 'passive_duration_s': 80.0, 'effects': [{'var': 'v_s1', 'value': 1}]},
            {'id': 'op_s2', 'location': 'L1', 'preconditions': [{'var': 'v_s1', 'value': 1}], 'active_duration_s': 15.0, 'occupies_hand': False, 'passive_duration_s': 80.0, 'effects': [{'var': 'v_s2', 'value': 1}]},
            {'id': 'op_ind', 'location': 'L2', 'active_duration_s': 60.0, 'effects': [{'var': 'v_ind', 'value': 1}]},
        ],
        'goals': {'predicates': [{'var': 'v_s2', 'value': 1}, {'var': 'v_ind', 'value': 1}]},
    }
    chained_plan = solve_task_graph(chained_spec)
    assert chained_plan['complete'] is True
    chained_ops = [s['op_id'] for s in chained_plan['ordered_steps'] if s['kind'] == 'operation']
    assert chained_ops[0] == 'op_s1'
    assert 'op_ind' in chained_ops
    assert chained_ops.index('op_s1') < chained_ops.index('op_s2')


def test_emergence_b_movable_capacity_batching_and_cyclic_return() -> None:
    """(b) `movable=True, capacity>=2` consolidates L1->L2, L2->L3, L3->L1 when travel_cost > load/unload."""
    def make_cycle_spec(leg_cost_s: float) -> dict:
        return {
            'agent_location': 'L1',
            'manip_duration_s': 10.0,
            'locations': [{'id': 'L1'}, {'id': 'L2'}, {'id': 'L3'}],
            'travel_cost': [
                {'from_location': 'L1', 'to_location': 'L2', 'cost_s': leg_cost_s},
                {'from_location': 'L2', 'to_location': 'L3', 'cost_s': leg_cost_s},
                {'from_location': 'L3', 'to_location': 'L1', 'cost_s': leg_cost_s},
            ],
            'entities': [
                {'id': 'e_cap', 'location': 'L1', 'movable': True, 'capacity': 3},
                {'id': 'e_a1', 'location': 'L1', 'movable': True, 'capacity': 0},
                {'id': 'e_a2', 'location': 'L1', 'movable': True, 'capacity': 0},
                {'id': 'e_a3', 'location': 'L1', 'movable': True, 'capacity': 0},
                {'id': 'e_b1', 'location': 'L2', 'movable': True, 'capacity': 0},
                {'id': 'e_b2', 'location': 'L2', 'movable': True, 'capacity': 0},
                {'id': 'e_c1', 'location': 'L3', 'movable': True, 'capacity': 0},
                {'id': 'e_c2', 'location': 'L3', 'movable': True, 'capacity': 0},
            ],
            'goals': {
                'entity_locations': [
                    {'entity_id': 'e_cap', 'location': 'L1'},
                    {'entity_id': 'e_a1', 'location': 'L2'},
                    {'entity_id': 'e_a2', 'location': 'L2'},
                    {'entity_id': 'e_a3', 'location': 'L2'},
                    {'entity_id': 'e_b1', 'location': 'L3'},
                    {'entity_id': 'e_b2', 'location': 'L3'},
                    {'entity_id': 'e_c1', 'location': 'L1'},
                    {'entity_id': 'e_c2', 'location': 'L1'},
                ],
                'require_empty_hand': True,
            },
        }

    # High spatial travel cost (60s per leg > 20s load/unload): emerges 1 cyclic tour L1->L2->L3->L1 (180s travel)
    high_travel_plan = solve_task_graph(make_cycle_spec(60.0))
    assert high_travel_plan['complete'] is True
    assert high_travel_plan['status'] == 'optimal'
    assert high_travel_plan['total_travel_cost_s'] == pytest.approx(180.0)
    used_cap = [
        s for s in high_travel_plan['ordered_steps']
        if s.get('target_host') == 'e_cap' or s.get('entity_id') == 'e_cap'
    ]
    assert len(used_cap) > 0
    # Compare against single-hand without e_cap (~11 legs * 60s = 660s travel + 140s manip = 800s vs 520s)
    assert high_travel_plan['estimated_finish_clock_s'] == pytest.approx(520.0)

    # Low spatial travel cost (2s per leg << 20s load/unload): exact same solver leaves e_cap untouched!
    low_travel_plan = solve_task_graph(make_cycle_spec(2.0))
    assert low_travel_plan['complete'] is True
    used_cap_low = [
        s for s in low_travel_plan['ordered_steps']
        if s.get('target_host') == 'e_cap' or s.get('entity_id') == 'e_cap'
    ]
    assert used_cap_low == []


def test_emergence_c_minimum_reset_monotonic_state_ordering() -> None:
    """(c) Threshold preconditions on `var_s` + costly remote reset at `L_reset` emerge monotonic order."""
    def make_threshold_spec(initial_s: int) -> dict:
        return {
            'agent_location': 'L1',
            'locations': [{'id': 'L1'}, {'id': 'L2'}, {'id': 'L_reset'}],
            'travel_cost': [
                {'from_location': 'L1', 'to_location': 'L2', 'cost_s': 10.0},
                {'from_location': 'L1', 'to_location': 'L_reset', 'cost_s': 90.0},
                {'from_location': 'L2', 'to_location': 'L_reset', 'cost_s': 90.0},
            ],
            'state_vars': [
                {'name': 'var_s', 'value': initial_s},
                {'name': 'var_g1', 'value': 0},
                {'name': 'var_g2', 'value': 0},
                {'name': 'var_g3', 'value': 0},
                {'name': 'var_g4', 'value': 0},
            ],
            'operations': [
                # Listed intentionally in descending threshold order so naive sequential order would reset repeatedly
                {
                    'id': 'op_high_L1',
                    'location': 'L1',
                    'preconditions': [{'var': 'var_s', 'op': 'le', 'value': 3}],
                    'effects': [
                        {'var': 'var_s', 'op': 'max', 'value': 3},
                        {'var': 'var_g1', 'op': 'set', 'value': 1},
                    ],
                    'active_duration_s': 10.0,
                },
                {
                    'id': 'op_mid_L1',
                    'location': 'L1',
                    'preconditions': [{'var': 'var_s', 'op': 'le', 'value': 2}],
                    'effects': [
                        {'var': 'var_s', 'op': 'max', 'value': 2},
                        {'var': 'var_g2', 'op': 'set', 'value': 1},
                    ],
                    'active_duration_s': 10.0,
                },
                {
                    'id': 'op_low_L2',
                    'location': 'L2',
                    'preconditions': [{'var': 'var_s', 'op': 'le', 'value': 1}],
                    'effects': [
                        {'var': 'var_s', 'op': 'max', 'value': 1},
                        {'var': 'var_g3', 'op': 'set', 'value': 1},
                    ],
                    'active_duration_s': 10.0,
                },
                {
                    'id': 'op_low_L1',
                    'location': 'L1',
                    'preconditions': [{'var': 'var_s', 'op': 'le', 'value': 1}],
                    'effects': [
                        {'var': 'var_s', 'op': 'max', 'value': 1},
                        {'var': 'var_g4', 'op': 'set', 'value': 1},
                    ],
                    'active_duration_s': 10.0,
                },
                {
                    'id': 'op_reset',
                    'location': 'L_reset',
                    'effects': [{'var': 'var_s', 'op': 'set', 'value': 0}],
                    'active_duration_s': 15.0,
                    'repeatable': True,
                },
            ],
            'goals': {
                'predicates': [
                    {'var': 'var_g1', 'value': 1},
                    {'var': 'var_g2', 'value': 1},
                    {'var': 'var_g3', 'value': 1},
                    {'var': 'var_g4', 'value': 1},
                ],
            },
        }

    # Subcase 1: var_s starts at 0 -> optimal schedule needs ZERO visits to L_reset!
    plan_zero = solve_task_graph(make_threshold_spec(0))
    assert plan_zero['complete'] is True
    seq_zero = [s['op_id'] for s in plan_zero['ordered_steps']]
    assert 'op_reset' not in seq_zero
    assert seq_zero.index('op_low_L1') < seq_zero.index('op_mid_L1') < seq_zero.index('op_high_L1')
    assert seq_zero.index('op_low_L2') < seq_zero.index('op_mid_L1')

    # Subcase 2: var_s starts at 3 -> executes op_high_L1 first, resets ONCE at L_reset, then monotonic 1 -> 2!
    plan_one_reset = solve_task_graph(make_threshold_spec(3))
    assert plan_one_reset['complete'] is True
    seq_one = [s['op_id'] for s in plan_one_reset['ordered_steps']]
    assert seq_one.count('op_reset') == 1
    assert seq_one.index('op_high_L1') < seq_one.index('op_reset')
    assert seq_one.index('op_reset') < seq_one.index('op_low_L1')
    assert seq_one.index('op_low_L1') < seq_one.index('op_mid_L1')


def test_emergence_d_deadlock_free_bounded_buffer_swap() -> None:
    """(d) Two full `capacity=1` locations requiring empty preprocessing + `L_buf (capacity=1)` emerge swap."""
    spec = {
        'agent_location': 'L1',
        'manip_duration_s': 10.0,
        'locations': [
            {
                'id': 'L1',
                'capacity': 1,
                'place_preconditions': [{'var': 'var_prep_1', 'op': 'eq', 'value': 1}],
            },
            {
                'id': 'L2',
                'capacity': 1,
                'place_preconditions': [{'var': 'var_prep_2', 'op': 'eq', 'value': 1}],
            },
            {
                'id': 'L_buf',
                'capacity': 1,
            },
        ],
        'travel_cost': [
            {'from_location': 'L1', 'to_location': 'L2', 'cost_s': 8.0},
            {'from_location': 'L1', 'to_location': 'L_buf', 'cost_s': 6.0},
            {'from_location': 'L2', 'to_location': 'L_buf', 'cost_s': 6.0},
        ],
        'state_vars': [
            {'name': 'var_prep_1', 'value': 0},
            {'name': 'var_prep_2', 'value': 0},
        ],
        'entities': [
            {'id': 'e_1', 'location': 'L1', 'movable': True, 'capacity': 0},
            {'id': 'e_2', 'location': 'L2', 'movable': True, 'capacity': 0},
        ],
        'operations': [
            {
                'id': 'op_prep_1',
                'location': 'L1',
                'preconditions': [{'var': 'occupancy:L1', 'op': 'eq', 'value': 0}],
                'effects': [{'var': 'var_prep_1', 'op': 'set', 'value': 1}],
                'active_duration_s': 12.0,
                'occupies_hand': True,
            },
            {
                'id': 'op_prep_2',
                'location': 'L2',
                'preconditions': [{'var': 'occupancy:L2', 'op': 'eq', 'value': 0}],
                'effects': [{'var': 'var_prep_2', 'op': 'set', 'value': 1}],
                'active_duration_s': 12.0,
                'occupies_hand': True,
            },
        ],
        'goals': {
            'predicates': [
                {'var': 'var_prep_1', 'value': 1},
                {'var': 'var_prep_2', 'value': 1},
            ],
            'entity_locations': [
                {'entity_id': 'e_1', 'location': 'L2'},
                {'entity_id': 'e_2', 'location': 'L1'},
            ],
            'require_empty_hand': True,
        },
    }
    plan = solve_task_graph(spec)
    assert plan['complete'] is True
    assert plan['status'] == 'optimal'

    # Replay every step to verify capacity <= 1, single hand, and empty-before-prep invariants at every instant
    occ = {'L1': 1, 'L2': 1, 'L_buf': 0}
    ent_loc = {'e_1': 'L1', 'e_2': 'L2'}
    prep = {'L1': 0, 'L2': 0}
    hand = None
    used_buffer = False

    for step in plan['ordered_steps']:
        if step['kind'] == 'pick':
            assert hand is None
            eid = step['entity_id']
            src = ent_loc[eid]
            occ[src] -= 1
            ent_loc[eid] = 'hand'
            hand = eid
        elif step['kind'] == 'place':
            eid = step['entity_id']
            dst = step['target_host']
            assert hand == eid
            assert occ[dst] + 1 <= 1, f'Capacity violated on {dst}'
            if dst in prep:
                assert prep[dst] == 1, f'Placed onto {dst} before empty preprocessing'
            if dst == 'L_buf':
                used_buffer = True
            occ[dst] += 1
            ent_loc[eid] = dst
            hand = None
        elif step['op_id'] == 'op_prep_1':
            assert hand is None and occ['L1'] == 0
            prep['L1'] = 1
        elif step['op_id'] == 'op_prep_2':
            assert hand is None and occ['L2'] == 0
            prep['L2'] = 1

    assert used_buffer is True
    assert ent_loc == {'e_1': 'L2', 'e_2': 'L1'}
    assert prep == {'L1': 1, 'L2': 1}
    assert hand is None


def test_emergence_e_observe_before_blind_remote_transfer() -> None:
    """(e) Unknown `var_x` + costly remote return of surplus automatically schedules low-cost observation first."""
    spec = {
        'agent_location': 'L1',
        'locations': [{'id': 'L1'}, {'id': 'L2'}],
        'travel_cost': [{'from_location': 'L1', 'to_location': 'L2', 'cost_s': 80.0}],
        'state_vars': [
            {'name': 'var_x', 'unknown': True, 'domain': [0, 1, 2]},
            {'name': 'var_held', 'value': 0},
        ],
        'operations': [
            {
                'id': 'op_obs_L1',
                'location': 'L1',
                'active_duration_s': 10.0,
                'occupies_hand': False,
                'resolves_unknowns': ['var_x'],
            },
            {
                'id': 'op_take_L2',
                'location': 'L2',
                'preconditions': [{'var': 'var_held', 'op': 'lt', 'value': 2}],
                'effects': [{'var': 'var_held', 'op': 'add', 'value': 1}],
                'active_duration_s': 10.0,
            },
            {
                'id': 'op_return_L2',
                'location': 'L2',
                'preconditions': [{'var': 'var_held', 'op': 'ge', 'value': 1}],
                'effects': [{'var': 'var_held', 'op': 'add', 'value': -1}],
                'active_duration_s': 10.0,
            },
            {
                'id': 'op_put_L1',
                'location': 'L1',
                'preconditions': [{'var': 'var_held', 'op': 'ge', 'value': 1}],
                'effects': [
                    {'var': 'var_held', 'op': 'add', 'value': -1},
                    {'var': 'var_x', 'op': 'add', 'value': 1},
                ],
                'active_duration_s': 10.0,
            },
            {
                'id': 'op_unput_L1',
                'location': 'L1',
                'preconditions': [
                    {'var': 'var_x', 'op': 'ge', 'value': 1},
                    {'var': 'var_held', 'op': 'lt', 'value': 2},
                ],
                'effects': [
                    {'var': 'var_x', 'op': 'add', 'value': -1},
                    {'var': 'var_held', 'op': 'add', 'value': 1},
                ],
                'active_duration_s': 10.0,
            },
        ],
        'goals': {
            'predicates': [
                {'var': 'var_x', 'op': 'eq', 'value': 2},
                {'var': 'var_held', 'op': 'eq', 'value': 0},
            ],
        },
    }
    plan = solve_task_graph(spec)
    assert plan['complete'] is True
    assert plan['status'] == 'contingent_optimal'
    assert plan['replan_after_observation'] is True
    assert plan['next_step']['op_id'] == 'op_obs_L1'
    assert plan['next_step']['replan_after_step'] is True

    # Expected cost: 10s (obs) + 1/3 * (200s [x=0] + 180s [x=1] + 0s [x=2]) = 136.67s << ~280s blind haul!
    assert plan['expected_remaining_s'] == pytest.approx(136.67, abs=0.2)
    branches = {b['observed']['var_x']: b for b in plan['contingent_branches']}
    assert set(branches) == {0, 1, 2}
    assert branches[2]['ordered_steps'] == []
    assert [s['op_id'] for s in branches[1]['ordered_steps']] == ['op_take_L2', 'op_put_L1']
    assert len(branches[0]['ordered_steps']) == 4

    # Open-domain unknown behind a prerequisite unlock sub-chain: schedules unlock -> obs checkpoint
    open_spec = {
        'agent_location': 'L1',
        'locations': [{'id': 'L1'}, {'id': 'L2'}],
        'travel_cost': [{'from_location': 'L1', 'to_location': 'L2', 'cost_s': 80.0}],
        'state_vars': [
            {'name': 'var_access', 'value': 0},
            {'name': 'var_u', 'unknown': True},
        ],
        'operations': [
            {
                'id': 'op_unlock',
                'location': 'L1',
                'effects': [{'var': 'var_access', 'value': 1}],
                'active_duration_s': 10.0,
            },
            {
                'id': 'op_inspect',
                'location': 'L1',
                'preconditions': [{'var': 'var_access', 'value': 1}],
                'resolves_unknowns': ['var_u'],
                'active_duration_s': 10.0,
            },
        ],
        'goals': {'predicates': [{'var': 'var_u', 'value': 2}]},
    }
    open_plan = solve_task_graph(open_spec)
    assert open_plan['status'] == 'observation_checkpoint'
    assert open_plan['replan_after_observation'] is True
    assert [s['op_id'] for s in open_plan['ordered_steps']] == ['op_unlock', 'op_inspect']


def test_delta_merge_state_memory_sync_and_mcp_proxy(tmp_path: Path) -> None:
    """Verify lightweight Delta Merge, Route A `state_memory` zero-config sync, and MCP `--planning-mode`."""
    class DummyRouteAStateMemory:
        def for_planner(self):
            return {
                'current_location': 'L1',
                'locations': ['L1', 'L2'],
                'visited_locations': {'L1': {'id': 'L1'}, 'L2': {'id': 'L2'}},
                'travel_cost': {'L1': {'L1': 0.0, 'L2': 42.0}, 'L2': {'L1': 42.0, 'L2': 0.0}},
                'travel_edges': [{'from_location': 'L1', 'to_location': 'L2', 'seconds': 42.0, 'samples': 1}],
                'robot_clock_s': 12.5,
            }

    out = tmp_path / 'run_tg'
    backend = MockRGBBackend(out)
    rec = Recorder(out, {'output': str(out)})
    harness = VisionHarness(
        backend, rec, Budget(wall_seconds=None), profile='basic-skills', agent_step_limit=20,
        goal_skill='none', planning_mode='task_graph',
    )
    harness.state_memory = DummyRouteAStateMemory()  # type: ignore[attr-defined]
    assert 'plan_task_graph' in harness.catalog
    skill_names = {entry['name'] for entry in harness.skills.catalog()}
    assert 'task-graph-planning' in skill_names

    harness.call('initialize', {}, 'req-init')

    # Step 1: initial compact call (0 action steps used; travel_cost fused automatically from state_memory!)
    res1 = harness.call(
        'plan_task_graph',
        {
            'graph': {
                'locations': [{'id': 'L1'}, {'id': 'L2'}],
                'travel_cost': [{'from_location': 'L1', 'to_location': 'L2', 'cost_s': 10.0}],
                'state_vars': [{'name': 'var_u', 'unknown': True}],
                'operations': [
                    {'id': 'op_obs', 'location': 'L1', 'resolves_unknowns': ['var_u'], 'active_duration_s': 5.0},
                    {
                        'id': 'op_act',
                        'location': 'L2',
                        'preconditions': [{'var': 'var_u', 'op': 'eq', 'value': 1}],
                        'effects': [{'var': 'var_done', 'value': 1}],
                        'active_duration_s': 10.0,
                    },
                ],
                'goals': {'predicates': [{'var': 'var_done', 'value': 1}]},
            },
        },
        'req-tg-1',
    )
    assert res1['ok'] is True
    assert harness.agent_steps == 0
    assert res1['plan']['next_step']['op_id'] == 'op_obs'
    assert res1['public_status']['external_memory_synced']['measured_travel_edges_count'] >= 1

    # Step 2: lightweight delta merge — pass ONLY resolved var_u=1 and completed_operations=['op_obs']!
    res2 = harness.call(
        'plan_task_graph',
        {
            'graph': {'state_vars': [{'name': 'var_u', 'value': 1, 'unknown': False}]},
            'completed_operations': ['op_obs'],
        },
        'req-tg-2',
    )
    assert res2['ok'] is True
    assert res2['plan']['complete'] is True
    assert res2['plan']['next_step']['op_id'] == 'op_act'
    # Verify measured travel cost 42.0s from state_memory overrode initial 10.0s estimate!
    assert res2['plan']['next_step']['travel_cost_s'] == pytest.approx(42.0)

    # Verify standalone MCP proxy `--planning-mode task_graph` against a baseline off-mode bridge
    baseline_tools = tool_specs('minimal', planning_mode='off')
    assert 'plan_task_graph' not in {t['name'] for t in baseline_tools}

    class DummyBridgeHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            body = json.dumps({'ready': True, 'closed': False, 'tools': baseline_tools, 'rpc_timeout_seconds': 30}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(('127.0.0.1', 0), DummyBridgeHandler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        mcp_srv = asyncio.run(
            create_server(f'http://127.0.0.1:{srv.server_port}', planning_mode='task_graph')
        )
        mcp_tools = asyncio.run(mcp_srv.list_tools())
        assert 'plan_task_graph' in {t.name for t in mcp_tools}
        call_res = asyncio.run(
            mcp_srv.call_tool(
                'plan_task_graph',
                {
                    'graph': {
                        'locations': [{'id': 'L1'}],
                        'operations': [{'id': 'op_local', 'location': 'L1', 'effects': [{'var': 'v_ok', 'value': 1}]}],
                        'goals': {'predicates': [{'var': 'v_ok', 'value': 1}]},
                    }
                },
            )
        )
        blocks = call_res[0] if isinstance(call_res, tuple) else call_res
        parsed_mcp = json.loads(blocks[0].text)
        assert parsed_mcp['ok'] is True
        assert parsed_mcp['plan']['next_step']['op_id'] == 'op_local'
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)
