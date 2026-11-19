"""Regression tests for the real-robot-observable feedback path."""
import asyncio
import base64
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock
from types import SimpleNamespace

try:
    from mcp import types
    from PIL import Image
    from manipulation_agent.agent.action_evidence import crop_at, digest, INSPECT_TOOL, repeated_access
    from manipulation_agent.evidence_server import Feedback, create_server, planner_parts, txt
except ImportError:
    types = None

from manipulation_agent.agent.planning_subagent import PlanningSubagent
from manipulation_agent.tools import tool_specs


def packet(rev, hand='empty', *, ref=False, poison=False, front=None):
    images, parts = [], []
    for view in ('front', 'back', 'left', 'right'):
        buf = io.BytesIO()
        image = front if view == 'front' and front is not None else Image.new('RGB', (120, 80), (33, 60, rev))
        image.save(buf, format='PNG')
        raw = buf.getvalue()
        images.append(dict(view=view, image_ref=f'{view}-{rev}', mime_type='image/png', width=image.width,
                           height=image.height, sha256=digest(raw)))
        parts.append(types.ImageContent(type='image', mimeType='image/png', data=base64.b64encode(raw).decode()))
    body = dict(ok=True, initialized=True, observation=dict(observation_mode='rgb_only', revision=rev, images=images))
    if hand is not None:
        body['hand'] = dict(state=hand, source='gripper_sensor')
    if ref:
        body['reference_image'] = dict(revision=0, image={**images[0], 'image_ref': 'old-front'})
        parts.append(parts[0])
    if poison:
        body.update(ok=False, error={'code': 'postcondition_error', 'stage': 'released_inside_payload_unconfirmed',
                                    'message': 'PRIVATE'}, score=99, effect={'cut': {'pieces_created': 423},
                                    'transfer': {'retained_objects': 79, 'private': 'PRIVATE'}})
        body['observation']['private'] = 'PRIVATE'
        body['observation']['images'][0]['mesh'] = 'PRIVATE'
        if 'hand' in body:
            body['hand']['grasp_source'] = {'entity': 'PRIVATE'}
    return types.CallToolResult(content=[txt(body), *parts, txt('PRIVATE')] if poison else [txt(body), *parts],
                                isError=poison, structuredContent={'private': 'PRIVATE'} if poison else None)


def cmd(op, rev, xy=(.52,.45)):
    return dict(primitive=op, revision=rev, target={'image_ref': f'front-{rev}', 'point': list(xy)})


def body(result):
    return json.loads(result.content[0].text)


@unittest.skipIf(types is None, 'requires MCP and Pillow')
class EvidenceTests(unittest.IsolatedAsyncioTestCase):
    def test_interrupted_or_unrefreshed_motor_cannot_publish_or_reinspect_its_pre_action_rgb(self):
        for code in ('episode_cancelled', 'episode_timeout', None):
            for mode in ('sensor_only', 'evidence'):
                f = Feedback(mode)
                f.receive('initialize', {}, packet(0))
                upstream = packet(0)
                if code:
                    body = json.loads(upstream.content[0].text)
                    body.update(ok=False, error={'code': code})
                    upstream.content[0] = txt(body)
                result = f.receive('act', cmd('grasp', 0), upstream)
                self.assertTrue(result.isError)
                self.assertEqual(len(result.content), 1)
                self.assertEqual(json.loads(result.content[0].text)['error']['code'], code or 'observation_unavailable')
                self.assertTrue(f.inspect({'revision': 0, 'image_ref': 'front-0', 'point': [.5,.5]}).isError)

    def test_motor_privacy_including_envelope(self):
        for mode in ('sensor_only','evidence'):
            for op in ('place_inside','cut','pour','toggle_on','open','grasp'):
                a,b=Feedback(mode),Feedback(mode)
                for f in (a,b):
                    f.receive('initialize',{},packet(0, 'holding'))
                x=a.receive('act',cmd(op,0),packet(1))
                y=b.receive('act',cmd(op,0),packet(1, poison=True))
                self.assertEqual(x.model_dump(),y.model_dump(),(mode,op))
                self.assertNotIn('PRIVATE',y.model_dump_json())
                self.assertIsNone(y.structuredContent)
                self.assertEqual(planner_parts(x)[1],planner_parts(y)[1])

    async def test_transport_failure_leaves_no_pre_action_image_to_inspect_after_reconnect(self):
        for broken in (RuntimeError('lost camera reply'), types.CallToolResult(content=[txt('motor unavailable')])):
            with tempfile.TemporaryDirectory() as tmp:
                upstream = SimpleNamespace(list_tools=AsyncMock(return_value=types.ListToolsResult(
                    tools=[types.Tool(**s) for s in tool_specs('basic-skills')])),
                    call_tool=AsyncMock(side_effect=[packet(0),broken]))
                path = Path(tmp) / 'state.json'
                server = await create_server(upstream, dict(mode='evidence', state=str(path)))
                await server.call_tool('initialize', {})
                result = await server.call_tool('act', cmd('grasp', 0))
                self.assertEqual(body(result)['error']['code'], 'observation_unavailable')
                self.assertEqual(len(result.content), 1)
                f = Feedback('evidence', path)
                self.assertIsNone(f.observation)
                self.assertTrue(f.inspect(dict(revision=0,image_ref='front-0',point=[.5,.5])).isError)

    def test_unlabelled_simulator_attachment_is_not_a_gripper_measurement(self):
        for mode in ('sensor_only', 'evidence'):
            for state in ('empty', 'holding'):
                original = packet(0, state)
                raw = json.loads(original.content[0].text)
                del raw['hand']['source']
                raw['hand']['grasp_source'] = {'private_object': 'fixture'}
                original.content[0] = txt(raw)
                result = Feedback(mode).receive('initialize', {}, original)
                self.assertEqual(body(result)['hand'], {'state':'unavailable'})
                self.assertNotIn('private_object', result.content[0].text)

    def test_grasp_occupancy_is_separate_from_selection(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'state.json'
            f=Feedback('evidence',path);f.receive('initialize',{},packet(0))
            original=f.receive('act',cmd('grasp',0),packet(1,'holding'))
            self.assertEqual(body(original)['visual_evidence']['rows'][0]['original']['image_ref'],'front-0')
            f=Feedback('evidence',path)
            again=f.receive('act',cmd('grasp',1,(.1,.9)),packet(2,'holding'))
            self.assertEqual(body(again)['visual_evidence']['rows'][0]['original']['point'],[.52,.45])
            released=f.receive('act',cmd('place_inside',2),packet(3,'empty',poison=True))
            self.assertNotIn('Grasp selection',[r['label'] for r in body(released)['visual_evidence']['rows']])
            self.assertEqual(body(released)['hand'],{'state':'empty'})
            f.receive('act',cmd('grasp',3),packet(4,'holding'))
            f.receive('initialize',{},packet(8,'holding'))
            self.assertIsNone(f.memory.grasp)

    def test_no_occupancy_sensor_and_delayed_duplicate(self):
        f=Feedback('evidence')
        f.receive('initialize',{},packet(0,None))
        a=f.receive('act',cmd('grasp',0),packet(1,None))
        self.assertEqual(body(a)['hand'],{'state':'unavailable'})
        b=f.receive('act',cmd('grasp',0),packet(1,None))
        self.assertEqual(a,b)
        f.receive('act',cmd('place_inside',1),packet(2,None))
        f.receive('initialize',{},packet(0,None))
        self.assertEqual(f.memory.grasp['image_ref'], 'front-0')
        self.assertEqual(f.observation['revision'],2)

    def test_visually_unchanged_unmeasured_grasps_preserve_earlier_selection(self):
        original = Image.new('RGB', (512, 512), 'black')
        moved = original.copy()
        moved.paste('white', (244, 244, 268, 268))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'state.json'
            f = Feedback('evidence', path)
            f.receive('initialize', {}, packet(0, None, front=original))
            f.receive('act', cmd('grasp', 0, (.5,.5)), packet(1, None, front=moved))
            f.receive('act', cmd('grasp', 1), packet(2, None, front=moved))
            f = Feedback('evidence', path)
            result = body(f.receive('act', cmd('grasp', 2), packet(3, None, front=moved, poison=True)))
            info = result['visual_evidence']
            self.assertEqual(info['repeated_similar_images']['count'], 2)
            self.assertEqual([r['original']['image_ref'] for r in info['rows']], ['front-0','front-2'])
            self.assertEqual(result['hand']['state'], 'unavailable')
            # A subsequent small local change is visible even with a nearly identical full-view average.
            result = body(f.receive('act', cmd('grasp', 3, (.5,.5)), packet(4, None, front=original)))
            self.assertNotIn('repeated_similar_images', result['visual_evidence'])
            self.assertEqual(f.memory.grasp['image_ref'], 'front-3')
            f.receive('act', cmd('place_inside', 4, (.5,.5)), packet(5, None, front=moved))
            self.assertIsNone(f.memory.grasp)

    def test_repeated_material_requests_do_not_imply_a_failed_action(self):
        for op in ('cut', 'pour'):
            f = Feedback('evidence')
            f.receive('initialize', {}, packet(0, None))
            for rev in range(2):
                result = f.receive('act', cmd(op, rev), packet(rev + 1, None))
                info = body(result)['visual_evidence']
                self.assertTrue(any(row['label'] == 'Prior ' + op + ' selection' for row in info['rows']))
                self.assertNotIn('repeated_similar_images', info)

    def test_small_color_change_at_selected_point_resets_grasp_hint(self):
        original = Image.new('RGB', (512, 512), (255, 0, 0))
        moved = original.copy()
        # Red and this green have the same luminance in Pillow.
        moved.paste((0, 130, 0), (251, 251, 261, 261))
        f = Feedback('evidence')
        f.receive('initialize', {}, packet(0, None, front=original))
        for rev in range(2):
            f.receive('act', cmd('grasp', rev, (.5,.5)), packet(rev + 1, None, front=original))
        self.assertEqual(f.memory.similar_count, 2)
        result = f.receive('act', cmd('grasp', 2, (.5,.5)), packet(3, None, front=moved))
        self.assertNotIn('repeated_similar_images', body(result)['visual_evidence'])
        self.assertEqual(f.memory.grasp['image_ref'], 'front-2')

    async def test_concurrent_finish_clears_rgb_for_reconnected_inspection(self):
        entered, closing = asyncio.Event(), asyncio.Event()
        async def upstream_call(name, args):
            if name == 'act':
                entered.set()
                await closing.wait()
                return packet(1)
            if name == 'finish':
                closing.set()
                return types.CallToolResult(content=[txt({'closed': True})])
            return packet(0)
        with tempfile.TemporaryDirectory() as tmp:
            upstream = SimpleNamespace(list_tools=AsyncMock(return_value=types.ListToolsResult(
                tools=[types.Tool(**s) for s in tool_specs('basic-skills')])), call_tool=upstream_call)
            config = dict(mode='evidence', state=str(Path(tmp) / 'state.json'))
            actor = await create_server(upstream, config)
            await actor.call_tool('initialize', {})
            action = asyncio.create_task(actor.call_tool('act', cmd('grasp', 0)))
            await asyncio.wait_for(entered.wait(), 2)
            finish = asyncio.create_task(actor.call_tool('finish', dict(outcome='aborted', reason='fixture')))
            _, closed = await asyncio.wait_for(asyncio.gather(action, finish), 2)
            self.assertTrue(body(closed)['closed'])
            resumed = await create_server(upstream, config)
            inspected = await resumed.call_tool('inspect_target', dict(revision=1, image_ref='front-1', point=[.5,.5]))
            self.assertTrue(inspected.isError)
            self.assertFalse(any(part.type == 'image' for part in inspected.content))

    def test_only_selected_geometry_and_readonly(self):
        f=Feedback('evidence')
        f.receive('initialize',{},packet(0))
        first=f.inspect(dict(revision=0,image_ref='left-0',point=[1.,0.]))
        src=body(first)['visual_evidence']['rows'][0]['original']
        self.assertEqual(src['pixel'],[119,0])
        self.assertEqual(src['box'],[8,0,120,80])
        self.assertEqual(len([p for p in first.content if p.type=='image']),5)
        self.assertEqual(f.observation['revision'],0)
        for args in (dict(revision=0,image_ref='old-front',point=[0,0]),dict(revision=1,image_ref='left-0',point=[0,0]),
                     dict(revision=0,image_ref='left-0',point=[True,0])):
            self.assertTrue(f.inspect(args).isError)
        self.assertIsNone(f.memory.last)

    def test_hashes_and_reference_label(self):
        f=Feedback('evidence');f.receive('initialize',{},packet(0))
        f.receive('act',cmd('cut',0),packet(1))
        res=f.receive('initialize',{'reference_image_ref':'old-front'},packet(1,ref=True))
        self.assertEqual(sum(x.type=='image' for x in res.content),6)
        self.assertEqual(sum(x.type=='image' for x in planner_parts(res)[0].content),5)
        res.content[-1].data='YQ=='
        with self.assertRaises(ValueError):planner_parts(res)
        broken=packet(2);broken.content[1].data='YQ=='
        with self.assertRaises(ValueError): f.receive('act',cmd('cut',1),broken)

    def test_access_needs_both_phase_matches_and_change(self):
        def step(op,point,level,view='front'):
            return dict(primitive=op,point=point,scene=[level]*256,area=[level]*256,view=view)
        a=step('open',[.3,.4],90);b=step('close',[.5,.3],110)
        self.assertTrue(repeated_access([a,b,a,b]))
        for seq in ([a,b], [a,b,a], [a,b,b,a], [a,b,a,dict(b,view='left')],
                    [a,b,dict(a,point=[.6,.4]),b], [a,b,dict(a,area=[40]*256),b],
                    [a,dict(b,scene=a['scene']),a,dict(b,scene=a['scene'])]):
            self.assertFalse(repeated_access(seq))

    def test_repeated_open_requests_show_only_own_recent_selections(self):
        f = Feedback('evidence')
        f.receive('initialize', {}, packet(0, None))
        ops = ['open', 'look', 'open', 'grasp', 'open']
        for rev, op in enumerate(ops):
            if op == 'look':
                result = f.receive('look', {'revision':rev}, packet(rev + 1, None))
            else:
                args = cmd(op, rev, ((rev+1)/6,.5))
                result = f.receive('act', args, packet(rev + 1, None, poison=True))
            info = body(result).get('visual_evidence', {})
            if rev < 4:
                self.assertNotIn('recent_access_requests', info)
        group = info['recent_access_requests']
        self.assertEqual([s['image_ref'] for s in group['selections']], ['front-0','front-2','front-4'])
        self.assertNotIn('repeated_access', info)
        self.assertIn('Earlier access selection', [row['label'] for row in info['rows']])
        self.assertNotIn('PRIVATE', result.content[0].text)
        bright = Image.new('RGB', (120, 80), 'white')
        f.receive('look', {'revision':5}, packet(6, None, front=bright))
        other = body(f.receive('act', cmd('open',6), packet(7,None,front=bright)))
        self.assertNotIn('recent_access_requests', other['visual_evidence'])
        for rev in range(7,15):
            f.receive('look', {'revision':rev}, packet(rev+1,None))
        expired = body(f.receive('act',cmd('open',15),packet(16,None)))
        self.assertNotIn('recent_access_requests', expired['visual_evidence'])

    def test_large_moving_panel_distinct_clicks_and_neighbour_negative(self):
        scene=[80]*256
        first=[125 if 36<=i<=68 else v for i,v in enumerate(scene)]
        other=[125 if 145<=i<=177 else v for i,v in enumerate(scene)]
        def row(op, xy, rgb):
            return dict(primitive=op,point=xy,scene=rgb,area=[80]*256,view='right')
        a=row('open',[.2,.3],scene);b=row('close',[.65,.3],first)
        c=row('open',[.4,.45],scene);d=row('close',[.55,.45],first)
        self.assertTrue(repeated_access([a,b,c,d]))
        self.assertFalse(repeated_access([a,b,c,dict(d,scene=other)]))
        self.assertFalse(repeated_access([a,b,c,dict(d,view='left')]))

    async def test_normal_mcp_and_planner_get_same_pixels(self):
        with tempfile.TemporaryDirectory() as root:
            upstream=SimpleNamespace(list_tools=AsyncMock(return_value=types.ListToolsResult(
                tools=[types.Tool(**spec) for spec in tool_specs('basic-skills')])),
                call_tool=AsyncMock(side_effect=[packet(0),packet(1,'holding',poison=True)]))
            server=await create_server(upstream,dict(mode='evidence',state=str(Path(root)/'s.json')))
            got={t.name:t.inputSchema for t in await server.list_tools()}
            self.assertEqual(got,{s['name']:s['inputSchema'] for s in [*tool_specs('basic-skills'),INSPECT_TOOL]})
            await server.call_tool('initialize',{})
            result=await server.call_tool('act',cmd('grasp',0))
            inspect=await server.call_tool('inspect_target',dict(revision=1,image_ref='left-1',point=[.2,.1]))
            self.assertEqual(upstream.call_tool.await_count,2)
            def request(payload):
                self.sent=payload
                return {'output':[{'type':'message','content':[{'type':'output_text',
                    'text':json.dumps({'nodes':[],'next':[],'memory':[]})}]}]}
            planner=PlanningSubagent(instruction='Sort items',model='fixture',effort='low',request=request,
                                     output=root,capabilities='visual')
            try:
                planner.observe('act',cmd('grasp',0),result)
                planner.observe('inspect_target',{},inspect)
                planner.consult('inspect before use')
                images=[p['image_url'].split(',',1)[1] for p in self.sent['input'][0]['content'] if p['type']=='input_image']
                self.assertEqual(images,[p.data for p in inspect.content if p.type=='image'])
                self.assertNotIn('PRIVATE',json.dumps(self.sent))
                stopped = types.CallToolResult(content=[txt({'ok':False,'error':{'code':'episode_cancelled'}})], isError=True)
                planner.observe('act', cmd('grasp',1), stopped)
                self.assertFalse(planner.consult('after interruption')['ok'])
                self.assertEqual(planner.images, [])
                planner.observe('inspect_target', {}, inspect)
                planner.observe('finish', {}, types.CallToolResult(content=[txt({'ok': True, 'closed': True})]))
                self.assertFalse(planner.consult('after closure')['ok'])
                self.assertFalse(planner.images)
            finally:planner.events.close()
