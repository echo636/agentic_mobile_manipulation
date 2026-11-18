"""Regression tests for the real-robot-observable feedback path."""
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


def packet(rev, hand='empty', *, ref=False, poison=False):
    images, parts = [], []
    for view in ('front', 'back', 'left', 'right'):
        buf = io.BytesIO()
        Image.new('RGB', (120, 80), (33, 60, rev)).save(buf, format='PNG')
        raw = buf.getvalue()
        images.append(dict(view=view, image_ref=f'{view}-{rev}', mime_type='image/png', width=120,
                           height=80, sha256=digest(raw)))
        parts.append(types.ImageContent(type='image', mimeType='image/png', data=base64.b64encode(raw).decode()))
    body = dict(ok=True, initialized=True, observation=dict(observation_mode='rgb_only', revision=rev, images=images))
    if hand is not None:
        body['hand'] = dict(state=hand)
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
        self.assertIsNone(f.memory.grasp)
        self.assertEqual(f.observation['revision'],2)

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
            finally:planner.events.close()
