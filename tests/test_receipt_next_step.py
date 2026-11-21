"""Harness option --receipt-next-step (2026-11-21): simulator-free tests."""
import asyncio
import copy
import io
import json
from unittest.mock import patch

from PIL import Image

from manipulation_agent.agent.receipt_next_step import STAGE, next_step


def refused(code, msg='x'):
    return {'ok': False, 'error': {'code': code, 'message': msg, 'world_may_have_changed': False}}


def proxy_texts(option, code, primitive):
    from manipulation_agent import mcp_server
    from manipulation_agent.tools import tool_specs
    buf = io.BytesIO(); Image.new('RGB', (64, 64), (90, 90, 90)).save(buf, format='JPEG'); img = buf.getvalue()

    def fetch(url, *a, **k):
        if url.endswith('/healthz'):
            return json.dumps({'ready': True, 'tools': tool_specs('basic-skills')}).encode()
        return img

    def rpc(url, name, arguments, rid, **kw):
        return {'ok': False, 'error': {'code': code, 'message': 'The executor cannot place, attach or release without '
                                       'a held object.', 'world_may_have_changed': False},
                'execution_budget': {'steps_remaining': 50}}

    async def run():
        with patch('manipulation_agent.mcp_server.fetch', side_effect=fetch), \
                patch('manipulation_agent.mcp_server.rpc', side_effect=rpc):
            server = await mcp_server.create_server('http://127.0.0.1:1', receipt_next_step_on=option)
            res = await server._tool_manager.get_tool('act').fn(
                primitive=primitive, revision=1, target={'image_ref': 'rgb-00001-front', 'point': [0.5, 0.5]})
            return [c.text for c in res if c.type == 'text']
    return asyncio.run(run())


def test_off_leaves_every_receipt_byte_identical():
    for code, prim in (('empty_hand', 'attach'), ('hand_occupied', 'open'), ('nothing_lifted', 'grasp')):
        from manipulation_agent.observations.boundary import without_wall_clock
        off = proxy_texts(False, code, prim)
        raw = {'ok': False, 'error': {'code': code, 'message': 'The executor cannot place, attach or release without '
                                      'a held object.', 'world_may_have_changed': False},
               'execution_budget': {'steps_remaining': 50}}
        assert off[0] == json.dumps(without_wall_clock(raw), ensure_ascii=False)     # byte-identical to the bridge text
    on = ''.join(proxy_texts(True, 'empty_hand', 'attach'))
    assert 'First grasp the part you want to attach' in on


def test_each_primitive_gets_its_operation_sentence():
    cases = {
        ('empty_hand', 'place_inside'): 'First grasp the object you want to place',
        ('empty_hand', 'attach'): 'First grasp the part you want to attach, then call attach/hang',
        ('empty_hand', 'wipe'): 'use a held tool or vessel',
        ('empty_hand', 'release'): 'nothing to release',
        ('hand_occupied', 'open'): 'Stage the held item on a nearby clear surface first',
        ('hand_occupied', 'grasp'): 'Put it down first (place_on_top on a nearby clear surface), then grasp.',
        ('hand_occupied', 'toggle_on'): 'select that object in an image where it is visible',
    }
    for (code, prim), text in cases.items():
        out = next_step(refused(code), 'act', {'primitive': prim})
        assert text in out['error']['message'], (code, prim)
        assert 'release it' not in out['error']['message'] and 'bulb' not in out['error']['message']


def test_other_codes_and_existing_hint_are_untouched():
    for code in ('nothing_lifted', 'invalid_arguments', 'not_placed'):
        r = refused(code)
        assert next_step(copy.deepcopy(r), 'act', {'primitive': 'grasp'}) == r
    assert next_step(refused('empty_hand'), 'look', {'primitive': 'attach'}) == refused('empty_hand')
    already = refused('hand_occupied', 'The executor cannot perform this action while carrying an object.' + STAGE)
    out = next_step(copy.deepcopy(already), 'act', {'primitive': 'close'})
    assert out['error']['message'].count('Stage the held item') == 1
