"""v12b: two-step-select rules (up to 3 zooms, latest replaces, nested zoom, rejection texts with call shapes) and the
navigation receipt / same-room helper."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest

from manipulation_agent import mcp_server
from manipulation_agent.tools.zoom_select import compose_region, zoom_image, zoom_to_full
from test_v12_harness_switches import _jpeg, _obs_result, _server


def _run(script):
    calls = []

    def rpc(name, arguments):
        calls.append((name, json.loads(json.dumps(arguments))))
        return _obs_result(len(calls))
    fetch, rpc_h = _server(rpc)

    async def run():
        with patch('manipulation_agent.mcp_server.fetch', side_effect=fetch), \
                patch('manipulation_agent.mcp_server.rpc', side_effect=rpc_h):
            server = await mcp_server.create_server('http://127.0.0.1:1', two_step_select=True)
            tools = server._tool_manager
            await tools.get_tool('look').fn(yaw_degrees=0, revision=0)

            async def call(tool, **kw):
                out = await tools.get_tool(tool).fn(**kw)
                return json.loads(out[0].text)
            return await script(call)
    return asyncio.run(run()), calls


def test_three_zooms_then_limit_text_names_latest_ref():
    async def script(call):
        return [await call('zoom', image_ref='rgb-00001-front', point=[0.5, 0.5]) for _ in range(4)]
    out, calls = _run(script)
    assert [o['ok'] for o in out] == [True, True, True, False]
    msg = out[3]['error']['message']
    assert 'Zoom limit reached' in msg and '"zoom_ref": "zoom-00003"' in msg and 'call look' in msg
    assert out[3]['error']['action_step_used'] is False
    assert [c[0] for c in calls] == ['look']


def test_only_latest_zoom_ref_usable_and_text_shows_shape():
    async def script(call):
        a = await call('zoom', image_ref='rgb-00001-front', point=[0.5, 0.5])
        b = await call('zoom', image_ref='rgb-00001-front', point=[0.2, 0.2])
        old = await call('act', primitive='grasp', revision=1, target={'zoom_ref': a['zoom_ref'], 'point': [0.5, 0.5]})
        bad = await call('act', primitive='grasp', revision=1, target={'zoom_ref': b['zoom_ref'], 'point': [2, 0.5]})
        ok = await call('act', primitive='grasp', revision=1, target={'zoom_ref': b['zoom_ref'], 'point': [0.5, 0.5]})
        return old, bad, ok
    (old, bad, ok), calls = _run(script)
    assert old['ok'] is False and 'Only the latest zoom_ref (zoom-00002)' in old['error']['message']
    assert '{"zoom_ref": "zoom-00002", "point": [x, y]}' in old['error']['message']
    assert bad['ok'] is False and 'normalized to the zoomed image' in bad['error']['message']
    assert ok['zoom_selection']['zoom_ref'] == 'zoom-00002'          # a malformed point did not use it up
    assert [c[0] for c in calls] == ['look', 'act']


def test_nested_zoom_maps_to_full_image():
    async def script(call):
        a = await call('zoom', image_ref='rgb-00001-front', point=[0.5, 0.5])
        b = await call('zoom', image_ref=a['zoom_ref'], point=[0.5, 0.5])
        act = await call('act', primitive='grasp', revision=1, target={'zoom_ref': b['zoom_ref'], 'point': [0.5, 0.5]})
        return a, b, act
    (a, b, act), calls = _run(script)
    assert b['ok'] and b['source_image_ref'] == 'rgb-00001-front'
    width = lambda r: r[2] - r[0]
    assert width(b['region']) == pytest.approx(width(a['region']) / 4, rel=0.05)
    assert calls[-1][1]['target']['image_ref'] == 'rgb-00001-front'
    assert calls[-1][1]['target']['point'] == pytest.approx([0.5, 0.5], abs=0.004)


def test_stale_ref_text_says_look_first():
    async def script(call):
        await call('look', yaw_degrees=0, revision=1)
        return await call('zoom', image_ref='rgb-00001-front', point=[0.5, 0.5])
    out, _ = _run(script)
    assert out['ok'] is False and 'call look first' in out['error']['message'] and 'rgb-00002-front' in out['error']['message']


def test_zoom_arguments_sent_to_look_or_act_get_the_right_shape():
    async def script(call):
        look = await call('look', image_ref='rgb-00001-front', point=[0.5, 0.5])
        act = await call('act', image_ref='rgb-00001-front', point=[0.5, 0.5])
        return look, act
    (look, act), calls = _run(script)
    assert look['ok'] is False and '"yaw_degrees"' in look['error']['message'] and 'zoom {"image_ref"' in look['error']['message']
    assert act['ok'] is False and '"primitive": <primitive>' in act['error']['message'] and '"target"' in act['error']['message']
    assert [c[0] for c in calls] == ['look']                          # neither reached the simulator


def test_rejection_texts_are_generic():
    import inspect
    src = inspect.getsource(mcp_server)
    for word in ('hotdog', 'plate', 'microwave', 'fridge', 'table', 'cabinet'):
        assert word not in mcp_server.ACT_SHAPE and word not in mcp_server.TARGET_SHAPE


def test_compose_region_identity_and_nesting():
    full = [0.0, 0.0, 1.0, 1.0]
    assert compose_region(full, [0.25, 0.25, 0.75, 0.75]) == [0.25, 0.25, 0.75, 0.75]
    parent = [0.4, 0.4, 0.6, 0.6]
    assert compose_region(parent, [0.0, 0.0, 1.0, 1.0]) == pytest.approx(parent)
    jpeg, region, clean = zoom_image(_jpeg(), [0.5, 0.5], with_clean=True)
    assert clean[:4] == b'\x89PNG' and zoom_to_full(region, [0.5, 0.5]) == pytest.approx([0.5, 0.5], abs=0.003)


def test_agy_hook_shape_hint_only_with_zoom():
    from manipulation_agent.clients.agy import HOOK_SCRIPT
    assert 'if "zoom" in ALLOWED_TOOLS' in HOOK_SCRIPT and "call ToolName='act'" in HOOK_SCRIPT


def test_room_of_points_vectorised_matches_scalar():
    torch = pytest.importorskip('torch')
    from types import SimpleNamespace
    from manipulation_agent.executors.omnigibson_rgb import room_of_points
    ins = torch.zeros((20, 20), dtype=torch.int64)
    ins[10:, 10:] = 3            # rows = y index, cols = x index (SegmentationMap convention)
    ins[:10, 10:] = 5
    seg = SimpleNamespace(room_ins_map=ins, map_resolution=0.1, map_size=20)
    scene = SimpleNamespace(seg_map=seg)
    pts = torch.tensor([[0.5, 0.5], [0.5, -0.5], [-0.5, 0.5], [5.0, 5.0]])
    assert room_of_points(scene, pts).tolist() == [3, 5, 0, 0]
    assert room_of_points(SimpleNamespace(), pts) is None


def test_stance_guard_counts_navigation_without_movement():
    from manipulation_agent.agent.stance_repeat import StanceRepeatGuard
    g = StanceRepeatGuard()
    nav = {'primitive': 'navigate_to', 'target': {'image_ref': 'rgb-1-front', 'point': [0.4, 0.2]}}
    still = {'ok': True, 'effect': {'status': 'completed', 'navigation_status': 'already_at_closest_approach'}}
    moved = {'ok': True, 'effect': {'status': 'completed', 'navigation_status': 'approached'}}
    assert g.observe('act', nav, still) is None and g.observe('act', nav, still) is None
    note = g.observe('act', nav, still)
    assert note and 'same place 3 times' in note['stance_repeat'] and 'different destination' in note['stance_repeat']
    assert g.observe('act', nav, moved) is None and not g.active          # a real move clears it


def test_nav_receipt_is_fact_only():
    import inspect
    from manipulation_agent.runtime import vision_harness
    src = inspect.getsource(vision_harness)
    assert "'already_at_closest_approach':'The base did not move; it is already at the closest feasible standing point for the selected target. Inspect the updated RGB.'" in src


def test_agy_hook_script_runs_and_switch_off_text_unchanged():
    """Execute the generated hook (a syntax error there would silently break every agy run)."""
    import subprocess, sys
    from manipulation_agent.clients.agy import HOOK_SCRIPT
    base = ['act', 'finish', 'initialize', 'list_skills', 'look', 'read_skill']

    def run(tools, name, args):
        script = HOOK_SCRIPT.replace('__ALLOWED_TOOLS_JSON__', json.dumps(tools))
        out = subprocess.run([sys.executable, '-c', script], input=json.dumps({'toolCall': {'name': name, 'args': args}}),
                             capture_output=True, text=True, check=True).stdout
        return json.loads(out)
    nav = {'ServerName': 'manipulation', 'ToolName': 'navigate_to'}
    off = run(base, 'call_mcp_tool', nav)
    assert off['decision'] == 'deny' and off['reason'] == (
        "MCP call to ServerName='manipulation' ToolName='navigate_to' is not permitted. "
        "Use ServerName='manipulation' with one of: ['act', 'finish', 'initialize', 'list_skills', 'look', 'read_skill'].")
    on = run(base + ['zoom'], 'call_mcp_tool', nav)
    assert '"primitive": "navigate_to"' in on['reason'] and "call ToolName='act'" in on['reason']
    assert run(base, 'call_mcp_tool', {'ServerName': 'manipulation', 'ToolName': 'act'})['decision'] == 'allow'
