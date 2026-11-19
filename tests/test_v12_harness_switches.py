"""v12 harness options (default off): --stance-repeat-guard, --echo-full-frame, --two-step-select."""
from __future__ import annotations

import asyncio
import inspect
import io
import json
from unittest.mock import patch

import pytest

from manipulation_agent import mcp_server
from manipulation_agent.agent.stance_repeat import StanceRepeatGuard, active_message
from manipulation_agent.tools import tool_specs
from manipulation_agent.tools.zoom_select import ZOOM_TOOL, two_step_catalog, zoom_image, zoom_to_full, zoom_window


def _jpeg(size=512, colour=(120, 90, 60)):
    from PIL import Image
    buf = io.BytesIO(); Image.new('RGB', (size, size), colour).save(buf, format='JPEG'); return buf.getvalue()


def _fail(primitive='grasp'):
    return ('act', {'primitive': primitive, 'target': {'image_ref': 'rgb-1-front', 'point': [0.5, 0.5]}},
            {'ok': False, 'error': {'code': 'nothing_lifted'}})


def test_switches_default_off_in_proxy():
    sig = inspect.signature(mcp_server.create_server)
    for name in ('stance_repeat_guard', 'echo_full_frame', 'two_step_select'):
        assert sig.parameters[name].default is False


def test_stance_guard_counts_same_primitive_regardless_of_point(tmp_path):
    g = StanceRepeatGuard(state_path=tmp_path / 's.json')
    notes = []
    for point in ([0.1, 0.1], [0.5, 0.5], [0.9, 0.9]):
        name, args, res = _fail()
        args['target']['point'] = point
        notes.append(g.observe(name, args, res))
    assert notes[:2] == [None, None] and 'failed 3 times in a row from this stance' in notes[2]['stance_repeat']
    assert g.active and active_message(tmp_path / 's.json') == g.message()
    # a completed look (or navigate_to) clears it
    g.observe('look', {'yaw_degrees': 30}, {'ok': True})
    assert not g.active and active_message(tmp_path / 's.json') is None


def test_stance_guard_resets_on_other_primitive_and_success():
    g = StanceRepeatGuard()
    g.observe(*_fail('grasp')); g.observe(*_fail('grasp')); g.observe(*_fail('open'))
    assert not g.active and g.count == 1
    g.observe('act', {'primitive': 'open'}, {'ok': True})
    assert g.count == 0
    # rejected before execution: not counted
    g.observe('act', {'primitive': 'grasp'}, {'ok': False, 'error': {'code': 'invalid_arguments', 'action_step_used': False}})
    assert g.count == 0


def test_stance_text_is_generic():
    text = StanceRepeatGuard().observe and __import__('manipulation_agent.agent.stance_repeat', fromlist=['x']).stance_message('grasp', 3)
    for word in ('hotdog', 'plate', 'fridge', 'table', 'sock', 'tray'):
        assert word not in text.lower()


def test_zoom_mapping_round_trip_and_clamp():
    left, top, size = zoom_window(512, 512, [0.5, 0.5])
    assert (left, top, size) == (192, 192, 128)
    assert zoom_window(512, 512, [0.0, 1.0])[:2] == (0, 384)
    jpeg, region = zoom_image(_jpeg(), [0.5, 0.5])
    from PIL import Image
    assert Image.open(io.BytesIO(jpeg)).size == (512, 512)
    # the zoomed centre maps back near the coarse point, corners to the region corners
    assert zoom_to_full(region, [0, 0]) == pytest.approx(region[:2])
    assert zoom_to_full(region, [1, 1]) == pytest.approx(region[2:])
    assert zoom_to_full(region, [0.5, 0.5]) == pytest.approx([0.5, 0.5], abs=0.003)


def test_two_step_catalog_is_idempotent_and_adds_zoom():
    base = tool_specs('minimal')
    cat = two_step_catalog(base)
    assert [s['name'] for s in cat][-1] == 'zoom' and two_step_catalog(cat) == cat
    act = next(s for s in cat if s['name'] == 'act')
    assert 'anyOf' in act['inputSchema']['properties']['target']
    assert next(s for s in base if s['name'] == 'act')['inputSchema']['properties']['target'].get('anyOf') is None


def test_full_frame_marked_is_side_by_side():
    from PIL import Image
    one = Image.open(io.BytesIO(mcp_server.full_frame_marked([_jpeg()], [0.3, 0.7])))
    two = Image.open(io.BytesIO(mcp_server.full_frame_marked([_jpeg(), _jpeg()], [0.3, 0.7])))
    assert one.size == (512, 512) and two.size == (1032, 512)
    assert 'whole-image' in mcp_server.full_frame_caption('single', ['rgb-1-front'], [0.3, 0.7])


def _obs_result(rev):
    return {'ok': True, 'observation': {'revision': rev, 'observation_mode': 'rgb_only', 'images': [
        {'view': 'front', 'image_ref': f'rgb-{rev:05d}-front', 'sha256': None, 'mime_type': 'image/jpeg'}]}}


def _server(rpc, **options):
    import hashlib
    image = _jpeg()

    def fetch(url, *a, **k):
        if url.endswith('/healthz'):
            return json.dumps({'ready': True, 'tools': tool_specs('minimal')}).encode()
        return image

    def rpc_with_hash(url, name, arguments, request_id, **kw):
        result = rpc(name, arguments)
        for frame in (result.get('observation') or {}).get('images') or []:
            frame['sha256'] = hashlib.sha256(image).hexdigest()
        return result
    return fetch, rpc_with_hash


def test_zoom_tool_and_act_conversion_through_proxy():
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
            z = json.loads((await tools.get_tool('zoom').fn(image_ref='rgb-00001-front', point=[0.5, 0.5]))[0].text)
            again = json.loads((await tools.get_tool('zoom').fn(image_ref='rgb-00001-front', point=[0.5, 0.5]))[0].text)
            out = await tools.get_tool('act').fn(primitive='grasp', revision=1,
                                                 target={'zoom_ref': z['zoom_ref'], 'point': [0.5, 0.5]})
            reused = json.loads((await tools.get_tool('act').fn(
                primitive='grasp', revision=2, target={'zoom_ref': z['zoom_ref'], 'point': [0.5, 0.5]}))[0].text)
            stale = json.loads((await tools.get_tool('zoom').fn(image_ref='rgb-00001-front', point=[0.5, 0.5]))[0].text)
            return z, again, json.loads(out[0].text), reused, stale
    z, again, act, reused, stale = asyncio.run(run())
    assert z['ok'] and z['zoom_ref'] == 'zoom-00001'
    assert again['ok'] is False and 'one zoom' in again['error']['message']
    assert [c[0] for c in calls] == ['look', 'act']          # zoom never reaches the simulator
    sent = calls[1][1]['target']
    assert sent['image_ref'] == 'rgb-00001-front' and sent['point'] == pytest.approx([0.5, 0.5], abs=0.003)
    assert act['zoom_selection']['zoom_ref'] == 'zoom-00001'
    assert reused['ok'] is False and reused['error']['action_step_used'] is False
    assert stale['ok'] is False and 'newest observation' in stale['error']['message']


def test_stance_guard_rewrites_blocked_deferral(tmp_path):
    n = {'k': 0}

    def rpc(name, arguments):
        n['k'] += 1
        if name == 'act':
            r = _obs_result(n['k']); r.update(ok=False, error={'code': 'nothing_lifted'})
            r['execution_budget'] = {'steps_remaining': 100}
            return r
        return {'ok': True, 'closed': True}
    fetch, rpc_h = _server(rpc)

    async def run():
        with patch('manipulation_agent.mcp_server.fetch', side_effect=fetch), \
                patch('manipulation_agent.mcp_server.rpc', side_effect=rpc_h):
            server = await mcp_server.create_server('http://127.0.0.1:1', finish_guard=True,
                                                    guard_state=str(tmp_path / 'g.json'), stance_repeat_guard=True,
                                                    stance_repeat_state=str(tmp_path / 's.json'))
            tools = server._tool_manager
            outs = [json.loads((await tools.get_tool('act').fn(
                primitive='grasp', revision=i, target={'image_ref': 'rgb-00001-front', 'point': [0.1 * i, 0.5]}))[0].text)
                for i in range(1, 4)]
            fin = json.loads((await tools.get_tool('finish').fn(outcome='blocked', reason='cannot'))[0].text)
            return outs, fin
    outs, fin = asyncio.run(run())
    assert 'stance_repeat' not in outs[1] and 'stance_repeat' in outs[2]
    assert fin['ok'] is False and 'from this stance' in fin['error']['message']
    assert 'Reinspect the newest RGB' not in fin['error']['message']


def test_echo_full_frame_puts_full_view_first_and_labels_crops():
    n = {'k': 0}

    def rpc(name, arguments):
        n['k'] += 1
        r = _obs_result(n['k'])
        if name == 'act':
            r.update(ok=False, error={'code': 'nothing_lifted'})
        return r
    fetch, rpc_h = _server(rpc)

    async def run(full):
        with patch('manipulation_agent.mcp_server.fetch', side_effect=fetch), \
                patch('manipulation_agent.mcp_server.rpc', side_effect=rpc_h):
            server = await mcp_server.create_server('http://127.0.0.1:1', enable_click_echo=True,
                                                    failure_look_again=True, echo_full_frame=full)
            out = await server._tool_manager.get_tool('act').fn(
                primitive='grasp', revision=1, target={'image_ref': 'rgb-00001-front', 'point': [0.3, 0.6]})
            return [c.text for c in out if getattr(c, 'type', '') == 'text'][1:]
    on, off = asyncio.run(run(True)), asyncio.run(run(False))
    assert on[0].startswith('Full view rgb-00001-front') and any(t.startswith('Full views: LEFT') for t in on)
    assert all(mcp_server.CROP_ONLY_NOTE in t for t in on if t.startswith(('Click echo', 'Look again')))
    assert not any('Full view' in t or mcp_server.CROP_ONLY_NOTE in t for t in off)
