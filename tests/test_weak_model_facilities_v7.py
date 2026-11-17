"""v7 facilities for weak models: argument tolerance (client adaptation, every arm),
selection assist / names (route A selection module, default off), shared wording."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from manipulation_agent.contracts import Budget
from manipulation_agent.executors import selection_assist as sa
from manipulation_agent.observations.boundary import ERROR_MESSAGES
from manipulation_agent.observations.mock_rgb import MockRGBBackend
from manipulation_agent.records import Recorder
from manipulation_agent.runtime.vision_harness import VisionHarness
from manipulation_agent.tools.tolerance import tolerate_arguments, unwrap

REPO = Path(__file__).resolve().parents[1]
ACT = {'primitive': 'grasp', 'target': {'image_ref': 'rgb-00003-left', 'point': [0.6, 0.5]}, 'revision': 2,
       'placement_yaw_degrees': None, 'placement_roll_degrees': None, 'wait_seconds': None}


# ---- argument tolerance (pure) ----

def test_well_formed_arguments_pass_through_unchanged():
    name, out, notes = tolerate_arguments('act', dict(ACT), 2)
    assert (name, out, notes) == ('act', ACT, [])
    name, out, notes = tolerate_arguments('look', {'yaw_degrees': 45, 'revision': 2}, 2)
    assert (name, out, notes) == ('look', {'yaw_degrees': 45, 'revision': 2}, [])


@pytest.mark.parametrize('raw', [
    json.dumps(ACT),
    {'Arguments': ACT},
    {'Arguments': json.dumps(ACT), 'ServerName': 'manipulation', 'ToolName': 'act'},
    {'Arguments': {'arguments': ACT}},
    {f'"{k}"': v for k, v in ACT.items()},
])
def test_client_wrappings_are_removed(raw):
    name, out, notes = tolerate_arguments('act', raw, 2)
    assert name == 'act' and out == ACT and notes


def test_quoted_keys_inside_target_are_cleaned():
    raw = {'"primitive"': 'grasp', '"target"': {'"image_ref"': 'rgb-00003-left', '"point"': [0.6, 0.5]},
           '"revision"': 2}
    _, out, _ = tolerate_arguments('act', raw, 2)
    assert out == ACT


def test_look_fixes_and_act_look_routing_and_missing_revision():
    assert tolerate_arguments('look', {'primitive': 'look', 'yaw_degrees': 90, 'revision': 9}, 9)[1] == \
        {'yaw_degrees': 90, 'revision': 9}
    name, out, notes = tolerate_arguments('act', {'primitive': 'look', 'yaw_degrees': -45}, 7)
    assert name == 'look' and out == {'yaw_degrees': -45, 'revision': 7}
    assert 'routed_act_look_to_look' in notes and 'filled_latest_revision' in notes
    _, out, notes = tolerate_arguments('act', {'primitive': 'grasp', 'target': ACT['target']}, 5)
    assert out['revision'] == 5 and out['wait_seconds'] is None


def test_unknown_shapes_are_left_for_normal_validation():
    assert unwrap('not json')[0] == 'not json'
    assert tolerate_arguments('act', ['x'], 1) == ('act', ['x'], [])


# ---- harness wiring with the mock backend ----

def _harness(tmp_path, **switches):
    out = tmp_path / 'ep'
    return VisionHarness(MockRGBBackend(out), Recorder(out, {'output': str(out)}), Budget(wall_seconds=None),
                         profile='minimal', agent_step_limit=20, goal_skill='none', **switches)


def test_tolerance_off_keeps_v6_rejection_and_on_accepts_wrapped_calls(tmp_path):
    off = _harness(tmp_path / 'off')
    init = off.call('initialize', {}, 'i')
    wrapped = {'Arguments': json.dumps({'yaw_degrees': 30, 'revision': init['observation']['revision']})}
    assert off.call('look', wrapped, 'l1')['error']['code'] == 'invalid_arguments'

    on = _harness(tmp_path / 'on', argument_tolerance=True)
    init = on.call('initialize', {}, 'i')
    wrapped = {'Arguments': json.dumps({'yaw_degrees': 30, 'revision': init['observation']['revision']})}
    assert on.call('look', wrapped, 'l1')['ok'] is True
    events = [json.loads(l) for l in (tmp_path / 'on/ep/events.jsonl').read_text().splitlines()]
    assert any(e['kind'] == 'arguments_normalized' for e in events)


def test_selection_extras_only_with_their_switches(tmp_path):
    adjustment = sa.public_adjustment('adjusted', image_ref='rgb-1', selected_point=[0.5, 0.5],
                                      adjusted_point=[0.52, 0.5], distance_px=10)
    for switches, expect_snap, expect_name in (({}, False, False),
                                               ({'selection_assist': True}, True, False),
                                               ({'selection_names': True}, False, True)):
        h = _harness(tmp_path / str(len(switches)) / str(expect_snap), **switches)
        init = h.call('initialize', {}, 'i')
        h.backend._last_selection_snap = adjustment
        h.backend._last_selected_category = 'counter'
        original = h.backend.execute_visual

        def keep_extras(*a, _orig=original, _b=h.backend, **k):
            result = _orig(*a, **k)
            _b._last_selection_snap = adjustment
            _b._last_selected_category = 'counter'
            return result
        h.backend.execute_visual = keep_extras
        ref = init['observation']['images'][0]['image_ref']
        res = h.call('act', {'primitive': 'open', 'target': {'image_ref': ref, 'point': [0.5, 0.5]},
                             'revision': init['observation']['revision']}, 'a')
        assert ('selection_adjustment' in res) is expect_snap
        assert ('selected_object_category' in res) is expect_name
    assert h.selection_assist is False and h.backend.selection_snap is False


# ---- snapping decision (pure) ----

def test_snap_decision_rules_and_public_record_has_no_names():
    assert sa.decide({}) == ('none', 0)
    assert sa.decide({'obj_a': (10, 10, 5.0)}) == ('adjusted', 'obj_a')
    assert sa.decide({'a': (1, 1, 1.0), 'b': (2, 2, 2.0)}) == ('multiple', 2)
    rec = sa.public_adjustment('adjusted', image_ref='rgb-00002-front', selected_point=[0.5, 0.5],
                               adjusted_point=[0.52, 0.49], distance_px=11.2)
    assert rec['applied'] is True and set(rec) == {'applied', 'image_ref', 'selected_point', 'adjusted_point',
                                                   'distance_px', 'radius_px', 'message'}
    multi = sa.public_adjustment('multiple', image_ref='rgb-1', selected_point=[0, 0], candidates=3)
    assert multi['applied'] is False and multi['candidates_nearby'] == 3
    assert sa.public_adjustment('none', image_ref='rgb-1', selected_point=[0, 0]) is None


def test_snap_radius_scales_and_centre_pixel_first():
    assert sa.snap_radius(512) == 24 and sa.snap_radius(1024) == 48
    pixels = [(0, 0, 1.0), (10, 0, 9.0), (5, 0, 4.0)]
    assert sa.candidate_order(pixels)[0] == (5, 0, 4.0)
    assert sa.SNAP_PRIMITIVES == {'grasp', 'open', 'close', 'toggle_on', 'toggle_off', 'cut'}


def test_executor_snaps_only_when_switched_on():
    src = (REPO / 'src/manipulation_agent/executors/omnigibson_rgb.py').read_text()
    assert "if getattr(self,'selection_snap',False) and primitive in" in src
    harness_src = (REPO / 'src/manipulation_agent/runtime/vision_harness.py').read_text()
    assert 'backend.selection_snap=self.selection_assist' in harness_src


# ---- MCP proxy and agy client adaptation ----

def test_string_tool_arguments_are_parsed_before_sdk_validation():
    from mcp import types
    from manipulation_agent.mcp_server import parse_string_tool_arguments
    msg = types.JSONRPCMessage(types.JSONRPCRequest(
        jsonrpc='2.0', id=1, method='tools/call',
        params={'name': 'finish', 'arguments': json.dumps({'outcome': 'blocked', 'reason': 'x'})}))
    assert parse_string_tool_arguments(msg) is True
    assert msg.root.params['arguments'] == {'outcome': 'blocked', 'reason': 'x'}
    types.CallToolRequest.model_validate({'method': 'tools/call', 'params': msg.root.params})
    assert parse_string_tool_arguments(msg) is False


def test_agy_rule_states_arguments_is_an_object():
    src = (REPO / 'src/manipulation_agent/clients/agy.py').read_text()
    assert 'do not put it in quotes as a string and do not nest another `Arguments` inside it' in src


# ---- shared wording ----

def test_shared_fixed_object_wording_names_the_cause_without_task_terms():
    message = ERROR_MESSAGES['fixed_object']
    assert 'fixed object' in message and "item's own body" in message
    for word in ('fridge', 'refrigerator', 'microwave', 'hotdog', 'shelf'):
        assert word not in message.lower()


def test_memory_enclosure_wording_does_not_read_as_empty():
    from manipulation_agent.agent.state_memory import _placed_by_robot
    text = _placed_by_robot({'items_inside': []})
    assert 'robot placed 0 item(s) inside' in text and 'other contents not tracked' in text
