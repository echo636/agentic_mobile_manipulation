"""Container matching by click heading and odometry stance (2026-11-21, coordinator 09:34). Simulator-free."""
import json
from collections import defaultdict
from pathlib import Path

from manipulation_agent.agent.state_memory import PRIVATE_ODOMETRY_KEY, StateMemory

DATA = Path(__file__).parent / 'data' / 'r1b_t7_cabinets_calls_53_89.json'


def receipt(rev, prim, yaw, xy=(0.0, 0.0)):
    return {'ok': True, 'effect': {'primitive': prim, 'status': 'completed'},
            'observation': {'revision': rev, 'images': [{'image_ref': f'rgb-{rev:05d}-{v}'} for v in ('front', 'back', 'left', 'right')]},
            PRIVATE_ODOMETRY_KEY: {'yaw_deg': yaw, 'xy': list(xy)}}


def act(prim, rev, view, point):
    return {'primitive': prim, 'revision': rev, 'target': {'image_ref': f'rgb-{rev:05d}-{view}', 'point': list(point)}}


def cid_of(mem, before):
    new = [c for c in mem.container_states if c not in before]
    return new[0] if new else None


def test_r1b_east_cabinet_gets_its_own_record():
    """The r1b sequence (calls 53-89, receipts and odometry as recorded): east and west never share a record."""
    seq = json.loads(DATA.read_text())
    mem = StateMemory()
    calls = []
    orig = mem._match_or_create_container

    def spy(*a, **k):
        cid = orig(*a, **k)
        calls.append(cid)
        return cid
    mem._match_or_create_container = spy
    by_record = defaultdict(set)
    for step in seq:
        calls.clear()
        mem.observe(step['name'], step['args'], step['result'])
        if step['args'].get('primitive') in ('open', 'close', 'place_inside') and calls and calls[0]:
            by_record[calls[0]].add(step['truth_for_scoring_only'])
    assert all(len(objs) == 1 for objs in by_record.values()), dict(by_record)
    assert any(objs == {'bceru_cabinet_east'} for objs in by_record.values())


def test_same_cabinet_open_front_close_left_is_one_record():
    mem = StateMemory()
    mem.observe('initialize', {}, receipt(0, 'initialize', 90.0))
    mem.observe('act', act('open', 0, 'front', (0.5, 0.4)), receipt(1, 'open', 90.0))
    opened = list(mem.container_states)
    mem.observe('look', {'revision': 1, 'yaw_degrees': -90}, receipt(2, 'look', 0.0))   # turned right by 90
    mem.observe('act', act('close', 2, 'left', (0.5, 0.45)), receipt(3, 'close', 0.0))   # left view now faces 90
    assert list(mem.container_states) == opened
    assert mem.container_states[opened[0]]['state'] == 'closed'


def test_turned_180_same_view_same_point_is_a_new_record():
    mem = StateMemory()
    mem.observe('initialize', {}, receipt(0, 'initialize', 0.0))
    mem.observe('act', act('open', 0, 'front', (0.5, 0.4)), receipt(1, 'open', 0.0))
    mem.observe('act', act('close', 1, 'front', (0.5, 0.4)), receipt(2, 'close', 0.0))
    first = set(mem.container_states)
    mem.observe('look', {'revision': 2, 'yaw_degrees': 180}, receipt(3, 'look', 180.0))
    mem.observe('act', act('open', 3, 'front', (0.5, 0.4)), receipt(4, 'open', 180.0))
    assert len(set(mem.container_states) - first) == 1          # the thing behind the robot is another container


def test_two_cabinets_in_different_directions_never_merge_by_the_closed_fallback():
    """The r1b mechanism in miniature: one closed cabinet here; opening another one 90 degrees away is not it."""
    mem = StateMemory()
    mem.observe('initialize', {}, receipt(0, 'initialize', 0.0))
    mem.observe('act', act('open', 0, 'front', (0.5, 0.4)), receipt(1, 'open', 0.0))
    mem.observe('act', act('close', 1, 'front', (0.5, 0.4)), receipt(2, 'close', 0.0))
    mem.observe('act', act('open', 2, 'left', (0.5, 0.4)), receipt(3, 'open', 0.0))
    assert len(mem.container_states) == 2


def test_unknown_heading_splits_rather_than_merges():
    mem = StateMemory()
    plain = lambda rev, prim: {k: v for k, v in receipt(rev, prim, 0.0).items() if k != PRIVATE_ODOMETRY_KEY}
    mem.observe('initialize', {}, plain(0, 'initialize'))
    mem.observe('act', act('open', 0, 'front', (0.2, 0.4)), plain(1, 'open'))
    mem.observe('act', act('close', 1, 'front', (0.2, 0.4)), plain(2, 'close'))
    mem.observe('act', act('open', 2, 'left', (0.8, 0.4)), plain(3, 'open'))   # no heading: no fallback merge
    assert len(mem.container_states) == 2


def test_odometry_never_reaches_the_public_view():
    mem = StateMemory()
    mem.observe('initialize', {}, receipt(0, 'initialize', 33.0, (1.5, 2.5)))
    mem.observe('act', act('open', 0, 'front', (0.5, 0.4)), receipt(1, 'open', 33.0, (1.5, 2.5)))
    text = json.dumps(mem.public_view(include_handoff=True)) + mem.render_memory_summary(max_chars=4000)
    assert 'heading' not in text and 'stance' not in text and '33.0' not in text and '2.5' not in text


def test_proxy_strips_odometry_before_the_model(tmp_path):
    """The real mcp_server proxy over a fake bridge that sends _private_odometry: no returned text contains it."""
    import asyncio, io
    from unittest.mock import patch
    from PIL import Image
    from manipulation_agent import mcp_server
    from manipulation_agent.tools import tool_specs
    import hashlib
    buf = io.BytesIO(); Image.new('RGB', (64, 64), (90, 90, 90)).save(buf, format='JPEG'); img = buf.getvalue()
    sha = hashlib.sha256(img).hexdigest()
    n = {'k': 0}

    def fetch(url, *a, **k):
        if url.endswith('/healthz'):
            return json.dumps({'ready': True, 'tools': tool_specs('basic-skills')}).encode()
        return img

    def rpc(url, name, arguments, rid, **kw):
        n['k'] += 1
        return {'ok': True, 'execution_budget': {'steps_remaining': 100},
                'observation': {'revision': n['k'], 'observation_mode': 'rgb_only', 'images': [
                    {'view': 'front', 'image_ref': f'rgb-{n["k"]:05d}-front', 'sha256': sha, 'mime_type': 'image/jpeg'}]},
                PRIVATE_ODOMETRY_KEY: {'yaw_deg': 123.4, 'xy': [9.876, 5.432]}}

    async def run(**opts):
        with patch('manipulation_agent.mcp_server.fetch', side_effect=fetch), \
                patch('manipulation_agent.mcp_server.rpc', side_effect=rpc):
            server = await mcp_server.create_server('http://127.0.0.1:1', **opts)
            t = server._tool_manager
            texts = []
            for name, args in (('look', {'yaw_degrees': 0, 'revision': 0}),
                               ('act', {'primitive': 'open', 'revision': 1, 'target': {'image_ref': 'rgb-00001-front', 'point': [0.5, 0.5]}})):
                res = await t.get_tool(name).fn(**args)
                texts += [c.text for c in res if c.type == 'text']
            return texts
    for opts in ({}, {'state_memory': True}, {'state_memory': True, 'place_names': True, 'place_return': True}):
        joined = '\n'.join(asyncio.run(run(**opts)))
        assert PRIVATE_ODOMETRY_KEY not in joined and '123.4' not in joined and '9.876' not in joined, opts


# ---------------------------------------------------------------- lines of sight (coordinator 09:58)
def test_close_from_another_stance_is_the_same_record_by_crossing_lines():
    """Cabinet at (2, 0): opened from (0, 0) looking east, closed from (2, -1.5) looking north."""
    mem = StateMemory()
    mem.observe('initialize', {}, receipt(0, 'initialize', 0.0, (0.0, 0.0)))
    mem.observe('act', act('open', 0, 'front', (0.5, 0.4)), receipt(1, 'open', 90.0, (2.0, -1.5)))
    mem.observe('act', act('close', 1, 'front', (0.5, 0.45)), receipt(2, 'close', 90.0, (2.0, -1.5)))
    assert len(mem.container_states) == 1
    assert next(iter(mem.container_states.values()))['state'] == 'closed'


def test_reopen_from_another_stance_matches_and_side_by_side_does_not():
    mem = StateMemory()
    mem.observe('initialize', {}, receipt(0, 'initialize', 0.0, (0.0, 0.0)))
    mem.observe('act', act('open', 0, 'front', (0.5, 0.4)), receipt(1, 'open', 0.0, (0.0, 0.0)))      # cabinet at (2, 0)
    mem.observe('act', act('close', 1, 'front', (0.5, 0.4)), receipt(2, 'close', 90.0, (2.0, -1.5)))
    mem.observe('act', act('open', 2, 'front', (0.5, 0.4)), receipt(3, 'open', 90.0, (2.0, -1.5)))   # from (2,-1.5) north
    mem.observe('act', act('close', 3, 'front', (0.5, 0.4)), receipt(4, 'close', 90.0, (2.0, -1.5)))
    assert len(mem.container_states) == 1
    # a second cabinet 1.2 m further east (3.2, 0): from (2, -1.5) its line of sight does not cross the first's lines
    import math
    x = 0.5 + math.tan(math.atan2(1.2, 1.5)) / 2          # horizontal click position of (3.2, 0) seen facing north
    mem.observe('act', act('open', 4, 'front', (x, 0.4)), receipt(5, 'open', 90.0, (2.0, -1.5)))
    assert len(mem.container_states) == 2


def test_place_inside_falls_back_to_the_one_open_record_but_open_never_does():
    mem = StateMemory()
    mem.observe('initialize', {}, receipt(0, 'initialize', 0.0, (0.0, 0.0)))
    mem.observe('act', act('open', 0, 'front', (0.5, 0.2)), receipt(1, 'open', 0.0, (0.0, 0.0)))
    # place from a stance and direction whose line does not cross the open line: still the only open record here
    mem.observe('act', act('place_inside', 1, 'back', (0.5, 0.6)), receipt(2, 'place_inside', 0.0, (0.2, 0.0)))
    assert len(mem.container_states) == 1
    mem.observe('act', act('close', 2, 'front', (0.5, 0.2)), receipt(3, 'close', 0.0, (0.0, 0.0)))
    mem.observe('act', act('open', 3, 'back', (0.5, 0.2)), receipt(4, 'open', 0.0, (0.0, 0.0)))     # other direction
    assert len(mem.container_states) == 2
