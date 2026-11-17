"""Unit tests for Route A: general spatial/object state memory, sliding image window,
rolling session handoff, and memory-aware FinishGuard verification.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
import re
import shutil
import tempfile
from unittest.mock import patch

import pytest

from manipulation_agent.agent.state_memory import OMITTED_IMAGE_PLACEHOLDER, StateMemory
from manipulation_agent.agent.vision_policy import (
    BASIC_SKILLS_PROMPT,
    MINIMAL_PROMPT,
    STATE_MEMORY_INSTRUCTION,
    system_prompt,
)
from manipulation_agent.clients import ClientConfig, get_adapter
from manipulation_agent.clients.events import completed_robot_action_event, parse_events, write_derived_events
from manipulation_agent.contracts import Budget
from manipulation_agent.observations.mock_rgb import MockRGBBackend
from manipulation_agent.records import Recorder
from manipulation_agent.reporting.efficiency_table import token_usage
from manipulation_agent.runtime.vision_harness import VisionHarness
from manipulation_agent.tools import tool_specs
from manipulation_agent.tools.finish_guard import FinishGuard

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_controller_module():
    spec = importlib.util.spec_from_file_location(
        'route_a_controller_mod', REPO_ROOT / 'scripts/run_codex_controller.py'
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _budget(remaining: int, limit: int = 200) -> dict:
    return {
        'scope': 'agent_steps',
        'steps_used': limit - remaining,
        'steps_remaining': remaining,
        'max_steps': limit,
    }


def test_default_off_switches_never_pollute_minimal_baseline(tmp_path: Path) -> None:
    """All new switches (--state-memory, --rolling-handoff-steps) default to off/0."""
    assert system_prompt('minimal', goal_skill='none') == MINIMAL_PROMPT
    assert system_prompt('basic-skills', goal_skill='none') == BASIC_SKILLS_PROMPT
    assert [t['name'] for t in tool_specs('minimal')] == ['initialize', 'look', 'act', 'finish']

    for profile in ('minimal', 'basic-skills', 'goal-tree', 'skills', 'skills_inline', 'workflow'):
        base_prompt = system_prompt(profile)
        assert STATE_MEMORY_INSTRUCTION not in base_prompt
        mem_prompt = system_prompt(profile, state_memory=True)
        assert mem_prompt == base_prompt + STATE_MEMORY_INSTRUCTION

    out = tmp_path / 'baseline_episode'
    backend = MockRGBBackend(out)
    rec = Recorder(out, {'output': str(out)})
    harness = VisionHarness(
        backend, rec, Budget(wall_seconds=None), profile='minimal', agent_step_limit=20, goal_skill='none'
    )
    assert harness.state_memory_enabled is False
    assert harness.state_memory is None
    init_res = harness.call('initialize', {}, 'req-init')
    assert 'state_memory' not in init_res
    rev = init_res['observation']['revision']
    ref = init_res['observation']['images'][0]['image_ref']
    act_res = harness.call(
        'act',
        {'primitive': 'open', 'target': {'image_ref': ref, 'point': [0.5, 0.5]}, 'revision': rev},
        'req-1',
    )
    assert act_res['ok'] is True
    assert 'state_memory' not in act_res
    assert not (out / 'state_memory.json').exists()

    controller = _load_controller_module()
    import inspect
    src = inspect.getsource(controller.main)
    assert "'--state-memory', choices=['off','on'], default='off'" in src
    assert "'--rolling-handoff-steps', type=int, default=0" in src


def test_zero_domain_overfitting_and_zero_simulator_imports() -> None:
    """Audit Route A modules and prompt additions against domain-specific vocabulary and simulator imports."""
    banned_stems = [
        'timed' + '_processes', 'car' + 'riers', 'appli' + 'ance', 'micro' + 'wave',
        'wash' + 'er', 'dry' + 'er', 'ket' + 'tle', 'tr' + 'ay', 'bas' + 'ket',
        'r' + 'ag', 'kni' + 'fe', 'so' + 'ak', 'clean_to' + '_dirty',
        'hot' + 'dog', 'fire' + 'wood', 'spr' + 'ayer', 'atom' + 'izer',
    ]
    pattern = re.compile(r'\b(?:' + '|'.join(banned_stems) + r')\b', re.IGNORECASE)
    audited_files = [
        REPO_ROOT / 'src/manipulation_agent/agent/state_memory.py',
        REPO_ROOT / 'src/manipulation_agent/tools/finish_guard.py',
    ]
    for path in audited_files:
        text = path.read_text(encoding='utf-8')
        match = pattern.search(text)
        assert match is None, f'Forbidden domain-specific token {match.group(0)!r} found in {path}'
        assert 'omnigibson' not in text.lower()
        assert 'pxr' not in text.lower()
    assert pattern.search(STATE_MEMORY_INSTRUCTION) is None


def test_spatial_memory_travel_matrix_and_planner_export() -> None:
    """Verify visited_locations, measured travel times, revisits, and Floyd-Warshall transitive export."""
    mem = StateMemory()
    mem.observe('initialize', {}, {'initialized': True, 'observation': {'revision': 0, 'images': []}})
    assert mem.current_location == 'loc_0'

    # Move loc_0 -> loc_1 (12.0m, 26.0s)
    mem.observe(
        'act',
        {
            'primitive': 'navigate_to',
            'target': {'image_ref': 'rgb-0-front', 'point': [0.5, 0.7]},
            'intent': 'Navigate to zone_north',
        },
        {
            'ok': True,
            'effect': {'primitive': 'navigate_to', 'status': 'completed', 'navigation_status': 'reached'},
            'observation': {'revision': 1, 'images': [{'image_ref': 'rgb-1-front'}]},
            'robot_time': {'this_call': {'travel_m': 12.0, 'travel_s': 26.0, 'manipulation_s': 0.0, 'wait_s': 0.0}, 'total_s': 26.0},
            'execution_budget': _budget(199),
        },
    )
    assert mem.current_location == 'loc_1'
    assert mem.visited_locations['loc_1']['hint'] == 'zone_north'

    # Move loc_1 -> loc_2 (8.0m, 18.0s)
    mem.observe(
        'act',
        {
            'primitive': 'navigate_to',
            'target': {'image_ref': 'rgb-1-right', 'point': [0.6, 0.7]},
            'intent': 'Navigate to zone_east',
        },
        {
            'ok': True,
            'effect': {'primitive': 'navigate_to', 'status': 'completed', 'navigation_status': 'reached'},
            'observation': {'revision': 2, 'images': [{'image_ref': 'rgb-2-front'}]},
            'robot_time': {'this_call': {'travel_m': 8.0, 'travel_s': 18.0, 'manipulation_s': 0.0, 'wait_s': 0.0}, 'total_s': 44.0},
            'execution_budget': _budget(198),
        },
    )
    assert mem.current_location == 'loc_2'

    # Revisit zone_north -> resolves back to loc_1 and averages edge loc_1<->loc_2 (18.0s and 20.0s -> 19.0s)
    mem.observe(
        'act',
        {
            'primitive': 'navigate_to',
            'target': {'image_ref': 'rgb-2-back', 'point': [0.5, 0.7]},
            'intent': 'Return to zone_north',
        },
        {
            'ok': True,
            'effect': {'primitive': 'navigate_to', 'status': 'completed', 'navigation_status': 'reached'},
            'observation': {'revision': 3, 'images': [{'image_ref': 'rgb-3-front'}]},
            'robot_time': {'this_call': {'travel_m': 8.4, 'travel_s': 20.0, 'manipulation_s': 0.0, 'wait_s': 0.0}, 'total_s': 64.0},
            'execution_budget': _budget(197),
        },
    )
    assert mem.current_location == 'loc_1'
    assert mem.visited_locations['loc_1']['visits'] == 2

    planner_data = mem.for_planner()
    assert planner_data['locations'] == ['loc_0', 'loc_1', 'loc_2']
    matrix = planner_data['travel_cost']
    assert matrix['loc_0']['loc_1'] == 26.0
    assert matrix['loc_1']['loc_2'] == 19.0
    # Shortest transitive chain loc_0 -> loc_1 -> loc_2 = 26.0 + 19.0 = 45.0s
    assert matrix['loc_0']['loc_2'] == 45.0


def test_hand_container_object_process_and_tool_state_tracking() -> None:
    """Verify public-receipt tracking for open/place_inside/close, postcondition drops, processes, and tool state."""
    mem = StateMemory()
    mem.observe('initialize', {}, {'initialized': True, 'observation': {'revision': 0, 'images': []}, 'robot': {'hand': 'empty'}})

    # Step 1: open enclosure at loc_0
    mem.observe(
        'act',
        {'primitive': 'open', 'target': {'image_ref': 'rgb-0-front', 'point': [0.45, 0.40]}},
        {
            'ok': True,
            'effect': {'primitive': 'open', 'status': 'completed'},
            'observation': {'revision': 1, 'images': []},
            'robot': {'hand': 'empty'},
            'robot_time': {'this_call': {'travel_m': 0.4, 'travel_s': 1.0, 'manipulation_s': 10.0, 'wait_s': 0.0}, 'total_s': 11.0},
            'execution_budget': _budget(199),
        },
    )
    assert 'container_1' in mem.open_containers()

    # Step 2 & 3: grasp item_1 and place_inside container_1
    mem.observe(
        'act',
        {'primitive': 'grasp', 'target': {'image_ref': 'rgb-1-left', 'point': [0.3, 0.6]}},
        {
            'ok': True,
            'effect': {'primitive': 'grasp', 'status': 'completed'},
            'observation': {'revision': 2, 'images': []},
            'robot': {'hand': 'holding_item'},
            'robot_time': {'this_call': {'travel_m': 0.2, 'travel_s': 0.8, 'manipulation_s': 10.0, 'wait_s': 0.0}, 'total_s': 21.8},
            'execution_budget': _budget(198),
        },
    )
    assert mem.hand_holding['status'] == 'holding_item'
    assert mem.hand_holding['item'] == 'item_1'

    mem.observe(
        'act',
        {'primitive': 'place_inside', 'target': {'image_ref': 'rgb-2-front', 'point': [0.46, 0.42]}},
        {
            'ok': True,
            'effect': {'primitive': 'place_inside', 'status': 'completed'},
            'observation': {'revision': 3, 'images': []},
            'robot': {'hand': 'empty'},
            'robot_time': {'this_call': {'travel_m': 0.2, 'travel_s': 0.6, 'manipulation_s': 10.0, 'wait_s': 0.0}, 'total_s': 32.4},
            'execution_budget': _budget(197),
        },
    )
    assert mem.hand_holding['status'] == 'empty'
    assert mem.container_states['container_1']['items_inside'] == ['item_1']
    assert mem.object_states['item_1']['location'] == 'inside:container_1'

    # Step 4: close container_1 + trigger active process
    mem.observe(
        'act',
        {'primitive': 'close', 'target': {'image_ref': 'rgb-3-front', 'point': [0.45, 0.40]}},
        {
            'ok': True,
            'effect': {
                'primitive': 'close',
                'status': 'completed',
                'process': {'any_process_running': True, 'messages': ['Background cycle started.']},
            },
            'observation': {'revision': 4, 'images': []},
            'robot': {'hand': 'empty'},
            'robot_time': {'this_call': {'travel_m': 0.0, 'travel_s': 0.0, 'manipulation_s': 10.0, 'wait_s': 0.0}, 'total_s': 42.4},
            'execution_budget': _budget(196),
        },
    )
    verified = mem.verified_closed_containers()
    assert 'container_1' in verified
    assert verified['container_1']['items_inside'] == ['item_1']
    assert mem.process_state['any_running'] is True

    # Step 5: grasp item_2, then failed place_on_top with released_placement_unconfirmed -> reconciles hand to empty & flags unverified
    mem.observe(
        'act',
        {'primitive': 'grasp', 'target': {'image_ref': 'rgb-4-right', 'point': [0.5, 0.5]}},
        {
            'ok': True,
            'effect': {
                'primitive': 'grasp',
                'status': 'completed',
                'process': {'any_process_running': True, 'messages': []},
                'tool_cleanliness': {'tool_dirtiness_level': 1, 'tool_state': 'tier_1_light_dust', 'cross_contamination_occurred': False, 'message': 'ok'},
            },
            'observation': {'revision': 5, 'images': []},
            'robot': {'hand': 'holding_item'},
            'robot_time': {'this_call': {'travel_m': 1.0, 'travel_s': 2.0, 'manipulation_s': 10.0, 'wait_s': 0.0}, 'total_s': 54.4},
            'execution_budget': _budget(195),
        },
    )
    assert mem.process_state['elapsed_active_s'] == 12.0
    assert mem.tool_state['level'] == 1

    mem.observe(
        'act',
        {'primitive': 'place_on_top', 'target': {'image_ref': 'rgb-5-right', 'point': [0.7, 0.5]}},
        {
            'ok': False,
            'error': {'code': 'postcondition_error', 'stage': 'released_placement_unconfirmed', 'message': 'released'},
            'observation': {'revision': 6, 'images': []},
            'robot': {'hand': 'empty'},
            'robot_time': {'this_call': {'travel_m': 0.0, 'travel_s': 0.0, 'manipulation_s': 10.0, 'wait_s': 0.0}, 'total_s': 64.4},
            'execution_budget': _budget(194),
        },
    )
    assert mem.hand_holding['status'] == 'empty'
    assert mem.unverified_items() == ['item_2']


def test_long_horizon_summary_budget_image_window_and_handoff_context() -> None:
    """Over 160 steps (640 images), summary stays ~100-150 tokens and sliding window caps active images."""
    mem = StateMemory()
    mem.observe('initialize', {}, {'initialized': True, 'observation': {'revision': 0, 'images': []}})

    # Stock and close an enclosure early at step 3 (tests >150-step retention)
    mem.observe('act', {'primitive': 'open', 'target': {'image_ref': 'rgb-0-front', 'point': [0.5, 0.5]}},
                {'ok': True, 'effect': {'primitive': 'open', 'status': 'completed'}, 'execution_budget': _budget(299, 300)})
    mem.observe('act', {'primitive': 'grasp', 'target': {'image_ref': 'rgb-1-left', 'point': [0.2, 0.5]}},
                {'ok': True, 'effect': {'primitive': 'grasp', 'status': 'completed'}, 'execution_budget': _budget(298, 300)})
    mem.observe('act', {'primitive': 'place_inside', 'target': {'image_ref': 'rgb-2-front', 'point': [0.5, 0.5]}},
                {'ok': True, 'effect': {'primitive': 'place_inside', 'status': 'completed'}, 'execution_budget': _budget(297, 300)})
    mem.observe('act', {'primitive': 'close', 'target': {'image_ref': 'rgb-3-front', 'point': [0.5, 0.5]}},
                {'ok': True, 'effect': {'primitive': 'close', 'status': 'completed'}, 'execution_budget': _budget(296, 300)})

    messages = [{'role': 'user', 'content': 'Complete all multi-zone placements.'}]
    for step_idx in range(5, 165):
        prim = 'navigate_to' if step_idx % 20 == 0 else ('grasp' if step_idx % 2 == 1 else 'place_on_top')
        t_m = 9.0 if prim == 'navigate_to' else 0.3
        t_s = 20.0 if prim == 'navigate_to' else 1.0
        mem.observe(
            'act',
            {'primitive': prim, 'target': {'image_ref': f'rgb-{step_idx - 1}-front', 'point': [0.5, 0.5]}},
            {
                'ok': True,
                'effect': {'primitive': prim, 'status': 'completed', 'navigation_status': 'reached'},
                'observation': {'revision': step_idx, 'images': [{'image_ref': f'rgb-{step_idx}-{v}'} for v in ('front', 'back', 'left', 'right')]},
                'robot_time': {'this_call': {'travel_m': t_m, 'travel_s': t_s, 'manipulation_s': 10.0, 'wait_s': 0.0}, 'total_s': float(step_idx * 12)},
                'execution_budget': _budget(300 - step_idx, 300),
            },
        )
        messages.append({
            'role': 'user',
            'content': [
                {'type': 'input_image', 'image_url': f'data:image/jpeg;base64,FAKE_{step_idx}_{v}'}
                for v in ('front', 'back', 'left', 'right')
            ],
        })

    summary = mem.render_memory_summary()
    assert len(summary) <= 620
    assert 'container_1@loc_0[closed, robot placed 1 item(s) inside; other contents not tracked]' in summary

    pruned, stats = mem.prune_image_window(messages, keep_latest_captures=4)
    assert stats['image_turns'] == 160
    assert stats['kept_images'] == 16
    assert stats['pruned_images'] == 624
    serialized_pruned = json.dumps(pruned)
    assert OMITTED_IMAGE_PLACEHOLDER in serialized_pruned
    assert '[StateMemory |' in serialized_pruned

    handoff = mem.build_handoff_context('Place all items in target enclosures.', segment_index=2, steps_remaining=136)
    assert 'Segment #2' in handoff
    assert '136 act/look steps' in handoff
    assert 'container_1 at loc_0 (1 verified item(s) inside: item_1)' in handoff
    assert 'DO NOT reopen' in handoff
    assert 'initialize({})' in handoff


def test_finish_guard_with_state_memory_protects_closed_enclosures_and_blocks_bailouts(tmp_path: Path) -> None:
    """Verify FinishGuard + state_memory avoids reopening stocked closed enclosures and blocks premature/in-place stops."""
    guard_path = tmp_path / 'guard_mem.json'
    guard = FinishGuard(guard_path, state_memory=True)
    guard.observe({'ok': True, 'initialized': True, 'execution_budget': _budget(100, 100)}, 'initialize', {})

    # 1. Stock and close container_1
    guard.observe({'ok': True, 'effect': {'primitive': 'open', 'status': 'completed'}, 'execution_budget': _budget(99, 100)}, 'act', {'primitive': 'open', 'target': {'image_ref': 'r0-front', 'point': [0.5, 0.5]}})
    guard.observe({'ok': True, 'effect': {'primitive': 'grasp', 'status': 'completed'}, 'robot': {'hand': 'holding_item'}, 'execution_budget': _budget(98, 100)}, 'act', {'primitive': 'grasp', 'target': {'image_ref': 'r1-left', 'point': [0.2, 0.6]}})
    guard.observe({'ok': True, 'effect': {'primitive': 'place_inside', 'status': 'completed'}, 'robot': {'hand': 'empty'}, 'execution_budget': _budget(97, 100)}, 'act', {'primitive': 'place_inside', 'target': {'image_ref': 'r2-front', 'point': [0.5, 0.5]}})
    guard.observe({'ok': True, 'effect': {'primitive': 'close', 'status': 'completed'}, 'robot': {'hand': 'empty'}, 'execution_budget': _budget(96, 100)}, 'act', {'primitive': 'close', 'target': {'image_ref': 'r3-front', 'point': [0.5, 0.5]}})

    # First achieved attempt defers WITH memory-verified closed enclosure notice ("DO NOT reopen")
    d1 = guard.deferral({'outcome': 'achieved', 'reason': 'All items placed and closed.'})
    assert d1 is not None
    msg1 = d1['error']['message']
    assert 'DO NOT reopen verified closed doors/enclosures' in msg1
    assert 'Memory-verified closed enclosures (do NOT reopen): container_1@loc_0 (1 verified item(s) inside: item_1' in msg1

    # Survives restart with state_memory intact, then accepts achieved after 1 fresh look WITHOUT reopening container_1!
    resumed = FinishGuard(guard_path)
    assert resumed.state_memory_enabled is True
    resumed.observe({'ok': True, 'effect': {'primitive': 'look', 'status': 'completed'}, 'execution_budget': _budget(95, 100)}, 'look', {'yaw_degrees': 0})
    assert resumed.deferral({'outcome': 'achieved', 'reason': 'Verified exterior in fresh RGB; container_1 verified closed in state_memory.'}) is None

    # 2. Blocks premature achieved while an active process is running or hand holds an item
    guard2 = FinishGuard(state_memory=True)
    guard2.observe({'ok': True, 'effect': {'primitive': 'grasp', 'status': 'completed', 'process': {'any_process_running': True, 'messages': ['Cycle active']}}, 'robot': {'hand': 'holding_item'}, 'robot_time': {'total_s': 20.0}, 'execution_budget': _budget(80, 100)}, 'act', {'primitive': 'grasp'})
    d_unres = guard2.deferral({'outcome': 'achieved', 'reason': 'done'})
    assert d_unres is not None
    assert 'Gripper is still holding an item' in d_unres['error']['message']
    assert 'active background process is still running' in d_unres['error']['message']
    # A mere `look` does NOT clear active unresolved hand/process issues!
    guard2.observe({'ok': True, 'effect': {'primitive': 'look', 'status': 'completed'}, 'execution_budget': _budget(79, 100)}, 'look', {})
    d_unres2 = guard2.deferral({'outcome': 'achieved', 'reason': 'done'})
    assert d_unres2 is not None
    assert 'Gripper is still holding an item' in d_unres2['error']['message']

    # 3. Blocks in-place same-primitive + look failure bailout until materially different actions are tried
    guard3 = FinishGuard(state_memory=True)
    guard3.observe({'ok': True, 'effect': {'primitive': 'open', 'status': 'completed'}, 'execution_budget': _budget(90, 100)}, 'act', {'primitive': 'open'})
    assert guard3.deferral({'outcome': 'blocked', 'reason': 'stuck'}) is not None
    guard3.observe({'ok': False, 'error': {'code': 'fixed_object', 'message': 'x'}, 'execution_budget': _budget(89, 100)}, 'act', {'primitive': 'grasp'})
    guard3.observe({'ok': False, 'error': {'code': 'fixed_object', 'message': 'x'}, 'execution_budget': _budget(88, 100)}, 'act', {'primitive': 'grasp'})
    guard3.observe({'ok': False, 'error': {'code': 'look_rotation_blocked', 'message': 'x'}, 'execution_budget': _budget(87, 100)}, 'look', {})
    # Even though 3 calls failed (`grasp`, `grasp`, `look`), only 1 manipulation primitive (`grasp`) was tried -> still deferred!
    d_same = guard3.deferral({'outcome': 'blocked', 'reason': 'still stuck'})
    assert d_same is not None
    assert 'repeated the same primitive (grasp)' in d_same['error']['message']


def test_vision_harness_state_memory_switch_and_handoff_reinit(tmp_path: Path) -> None:
    """Verify VisionHarness --state-memory on attaches public state_memory and preserves it across re-initialize."""
    out = tmp_path / 'harness_mem'
    backend = MockRGBBackend(out)
    backend._get_held = lambda: None  # type: ignore[attr-defined]
    rec = Recorder(out, {'output': str(out)})
    harness = VisionHarness(
        backend, rec, Budget(wall_seconds=None), profile='minimal', agent_step_limit=30,
        goal_skill='none', state_memory=True,
    )
    init1 = harness.call('initialize', {}, 'req-init-1')
    assert init1['ok'] is True
    assert 'state_memory' in init1
    assert 'handoff_context' not in init1['state_memory']

    rev = init1['observation']['revision']
    ref = init1['observation']['images'][0]['image_ref']
    act1 = harness.call('act', {'primitive': 'open', 'target': {'image_ref': ref, 'point': [0.5, 0.5]}, 'revision': rev}, 'req-act-1')
    assert act1['ok'] is True
    assert act1['state_memory']['open_containers'] == ['container_1']
    assert (out / 'state_memory.json').exists()

    # Simulate rolling session handoff calling initialize({}) mid-episode (0 steps charged!)
    steps_before = harness.agent_steps
    init2 = harness.call('initialize', {}, 'req-init-2')
    assert harness.agent_steps == steps_before
    assert 'handoff_context' in init2['state_memory']
    assert 'container_1@loc_0' in init2['state_memory']['handoff_context']


@pytest.mark.skipif(importlib.util.find_spec('mcp') is None, reason='Requires MCP SDK')
def test_mcp_server_rolling_handoff_checkpoint_and_controller_adapters(tmp_path: Path) -> None:
    """Verify MCP rolling_handoff_steps pauses after N steps without blocking finish, and adapters prepare fresh handoffs."""
    from manipulation_agent.mcp_server import create_server

    bridge_calls: list[str] = []

    def fake_rpc(url, name, arguments, request_id, **kwargs):
        bridge_calls.append(name)
        if name == 'finish':
            return {'ok': True, 'closed': True, 'agent_outcome': arguments.get('outcome')}
        return {
            'ok': True,
            'effect': {'primitive': arguments.get('primitive', name), 'status': 'completed'},
            'execution_budget': _budget(100 - len(bridge_calls), 100),
        }

    async def _exercise_mcp():
        health = json.dumps({'ready': True, 'tools': tool_specs('minimal')}).encode()
        with patch('manipulation_agent.mcp_server.fetch', return_value=health), \
             patch('manipulation_agent.mcp_server.rpc', side_effect=fake_rpc):
            server = await create_server(
                'http://127.0.0.1:1', state_memory=True, rolling_handoff_steps=2
            )
            invoke = lambda name, **kw: server._tool_manager.get_tool(name).fn(**kw)

            r1 = json.loads((await invoke('act', primitive='open'))[0].text)
            assert r1['ok'] is True
            assert 'state_memory' in r1
            assert 'rolling_handoff' not in r1

            r2 = json.loads((await invoke('act', primitive='close'))[0].text)
            assert r2['ok'] is True
            assert r2['rolling_handoff']['due'] is True

            # 3rd act in same session is intercepted with rolling_handoff_checkpoint (0 bridge calls!)
            r3 = json.loads((await invoke('act', primitive='grasp'))[0].text)
            assert r3['ok'] is False
            assert r3['error']['code'] == 'rolling_handoff_checkpoint'
            assert r3['error']['action_step_used'] is False
            assert bridge_calls == ['act', 'act']

            # Finish remains unobstructed if task completed right at checkpoint
            fin = json.loads((await invoke('finish', outcome='achieved', reason='done'))[0].text)
            assert fin['closed'] is True
            return r1, r2, r3

    r1, r2, r3 = asyncio.run(_exercise_mcp())

    # Controller rolling_handoff_due + multi-segment token accounting + adapter.prepare_handoff
    controller = _load_controller_module()
    compat_events = [
        {'type': 'item.completed', 'item': {'id': 'c1', 'type': 'mcp_tool_call', 'tool': 'act', 'arguments': {'primitive': 'open'}, 'result': {'content': [{'type': 'text', 'text': json.dumps(r1)}]}}},
        {'type': 'item.completed', 'item': {'id': 'c2', 'type': 'mcp_tool_call', 'tool': 'act', 'arguments': {'primitive': 'close'}, 'result': {'content': [{'type': 'text', 'text': json.dumps(r2)}]}}},
        {'type': 'item.completed', 'item': {'id': 'c3', 'type': 'mcp_tool_call', 'tool': 'act', 'arguments': {'primitive': 'grasp'}, 'result': {'content': [{'type': 'text', 'text': json.dumps(r3)}]}}},
    ]
    assert controller.segment_robot_calls(compat_events) == 2
    assert controller.rolling_handoff_due(
        compat_events, returncode=0, finish_received=False, timed_out=False, rolling_handoff_steps=2
    ) is True
    assert controller.rolling_handoff_due(
        compat_events, returncode=0, finish_received=False, timed_out=False, rolling_handoff_steps=0
    ) is False

    mem = StateMemory()
    assert mem.ingest_compat_events(compat_events) == 2
    assert 'container_1' in mem.verified_closed_containers()

    # Check prepare_handoff on Claude & Agy adapters + multi-segment token flush
    claude_cfg = ClientConfig('claude', 'sonnet', 'Task', '/usr/bin/python', ['-m', 'manipulation_agent.mcp_server'], tmp_path / 'c')
    (tmp_path / 'c').mkdir()
    claude_adapter = get_adapter('claude')
    c_proj = claude_adapter.prepare_project(claude_cfg, 'Inst', ['initialize', 'act', 'finish'])
    c_handoff = claude_adapter.prepare_handoff(c_proj, 'HANDOFF_PROMPT')
    assert '--resume' not in c_handoff.argv
    assert c_handoff.stdin == 'HANDOFF_PROMPT'

    if shutil.which('bwrap'):
        fake_home = tmp_path / 'fake_home'
        (fake_home / '.gemini' / 'antigravity-cli' / 'builtin').mkdir(parents=True)
        agy_out = tmp_path / 'agy_ep'
        agy_out.mkdir()
        agy_adapter = get_adapter('agy')
        with patch('pathlib.Path.home', return_value=fake_home):
            a_proj = agy_adapter.prepare_project(
                ClientConfig('agy', 'gemini-3.8-flash-low', 'Task', '/usr/bin/python', ['-m', 'manipulation_agent.mcp_server'], agy_out),
                'Inst', ['initialize', 'act', 'finish'],
            )
        a_handoff = agy_adapter.prepare_handoff(a_proj, 'HANDOFF_PROMPT')
        assert '--conversation' not in a_handoff.argv
        assert 'HANDOFF_PROMPT' in a_handoff.argv[-1]

    # Verify multi-segment interrupted Agy streams flush tokens cleanly without wiping segment 1
    agy_multi_dir = tmp_path / 'agy_tokens'
    agy_multi_dir.mkdir()
    agy_raw = [
        {'event': 'init', 'conversation_id': '11111111-1111-1111-1111-111111111111'},
        {'event': 'step_update', 'step_update': {'conversation_id': '11111111-1111-1111-1111-111111111111', 'step_index': 1, 'step_type': 'agent_response', 'state': 'DONE', 'text_delta': 'a', 'usage': {'input_tokens': 100, 'cache_read_tokens': 400, 'output_tokens': 20}}},
        {'event': 'step_update', 'step_update': {'conversation_id': '11111111-1111-1111-1111-111111111111', 'step_index': 2, 'step_type': 'tool', 'state': 'DONE', 'tool_info': {'name': 'call_mcp_tool', 'parameters': {'ServerName': 'manipulation', 'ToolName': 'act', 'Arguments': {'primitive': 'open'}}, 'output': '{"ok": true}'}}},
        {'event': 'init', 'conversation_id': '22222222-2222-2222-2222-222222222222'},
        {'event': 'result', 'result': {'status': 'SUCCESS', 'usage': {'input_tokens': 80, 'cache_read_tokens': 200, 'output_tokens': 15}}},
    ]
    assert completed_robot_action_event(agy_raw[2]) is True
    raw_file = agy_multi_dir / 'model_events.jsonl'
    raw_file.write_text(''.join(json.dumps(x) + '\n' for x in agy_raw))
    parsed = parse_events(raw_file, 'agy')
    write_derived_events(agy_multi_dir, parsed, 'agy')
    totals = token_usage(agy_multi_dir)
    assert totals['input_tokens'] == 500 + 280
    assert totals['cached_input_tokens'] == 400 + 200
    assert totals['output_tokens'] == 20 + 15
