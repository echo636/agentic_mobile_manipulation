"""Planner wiring contracts, not simulator success or planning-quality tests."""
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

HAS_MCP = importlib.util.find_spec('mcp') is not None
if HAS_MCP:
    from mcp import types

from manipulation_agent.agent.planning_subagent import PlanningSubagent, parse_plan
from manipulation_agent.planner_server import create_server
from manipulation_agent.tools import tool_specs


PLAN = {'nodes': [{'id': 'a', 'work': 'All cups are on the tray', 'after': []},
                  {'id': 'b', 'work': 'Tray and cups are on the table', 'after': ['a']}],
        'next': ['a']}


def response(plan):
    return {'status': 'completed', 'usage': {'total_tokens': 12}, 'output': [
        {'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(plan)}]}]}


def rgb_result(revision):
    frames, pixels = [], []
    for view in ['front', 'back', 'left', 'right']:
        raw = f'{view}-{revision}'.encode()
        frames.append({'view': view, 'image_ref': f'rgb-{revision}-{view}',
                       'mime_type': 'image/png', 'sha256': hashlib.sha256(raw).hexdigest()})
        pixels.append(types.ImageContent(type='image', mimeType='image/png',
            data=base64.b64encode(raw).decode(), _meta={'codex/imageDetail': 'original'}))
    body = {'ok': True, 'observation': {'observation_mode': 'rgb_only', 'revision': revision, 'images': frames},
            'effect': {'message': 'Container moved'}, 'runtime': {'tool_seconds': 4.}}
    return types.CallToolResult(content=[types.TextContent(type='text', text=json.dumps(body)), *pixels])


@unittest.skipUnless(HAS_MCP, 'Install the pinned MCP extra for planner transport tests')
class PlannerContracts(unittest.IsolatedAsyncioTestCase):
    async def test_proxy_preserves_images_and_uses_latest_rgb_without_acting(self):
        payloads = []
        def request(payload):
            payloads.append(payload)
            return response(PLAN)
        with tempfile.TemporaryDirectory() as directory:
            planner = PlanningSubagent(instruction='Serve cups.', model='test', effort='high',
                request=request, output=directory, capabilities='grasp, place')
            upstream = SimpleNamespace(
                list_tools=AsyncMock(return_value=types.ListToolsResult(
                    tools=[types.Tool(**spec) for spec in tool_specs('basic-skills')])),
                call_tool=AsyncMock(side_effect=[rgb_result(0), rgb_result(1)]))
            server = await create_server(upstream, planner)
            initial = await server.call_tool('initialize', {})
            self.assertEqual(len([part for part in initial.content if part.type == 'image']), 4)
            acted = await server.call_tool('act', {'primitive': 'grasp', 'revision': 0})
            self.assertEqual(acted, rgb_result(1))
            result = await server.call_tool('plan_task', {'progress': 'Tray found; carrying it.'})
            self.assertEqual(json.loads(result[0].text), {'ok': True, **PLAN})
            self.assertEqual(upstream.call_tool.await_count, 2)  # Planning made no simulator calls.
            content = payloads[0]['input'][0]['content']
            images = [part['image_url'] for part in content if part['type'] == 'input_image']
            self.assertEqual(images, ['data:image/png;base64,' + part.data for part in acted.content if part.type == 'image'])
            self.assertEqual(len(images), 4)
            self.assertNotIn('tools', payloads[0])  # Planner cannot execute robot actions.
            context = json.loads(content[0]['text'])
            self.assertEqual(context['actions_since_plan'], 1)
            self.assertEqual(context['recent_action_feedback'][0]['arguments']['primitive'], 'grasp')
            planner.events.close()
            saved = PlanningSubagent(instruction='Serve cups.', model='test', effort='high',
                request=request, output=directory, capabilities='grasp, place')
            self.assertEqual(saved.plan, PLAN)

    async def test_failed_planning_preserves_previous_plan_and_allows_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'plan.json').write_text(json.dumps(PLAN))
            planner = PlanningSubagent(instruction='Serve cups.', model='test', effort='high',
                request=lambda _: response({'nodes': [{'id': 'c', 'work': 'cycle', 'after': ['c']}], 'next': ['c']}),
                output=directory, capabilities='grasp')
            upstream = SimpleNamespace(
                list_tools=AsyncMock(return_value=types.ListToolsResult(
                    tools=[types.Tool(**spec) for spec in tool_specs('basic-skills')])),
                call_tool=AsyncMock(side_effect=[rgb_result(0), rgb_result(1)]))
            server = await create_server(upstream, planner)
            await server.call_tool('initialize', {})
            result = await server.call_tool('plan_task', {'progress': 'Reconsider route.'})
            self.assertFalse(json.loads(result[0].text)['ok'])
            self.assertEqual(planner.plan, PLAN)
            acted = await server.call_tool('act', {'primitive': 'grasp', 'revision': 0})
            self.assertFalse(acted.isError)
            planner.events.close()

    def test_next_segment_must_follow_dependencies(self):
        self.assertEqual(parse_plan(response(PLAN)), PLAN)
        with self.assertRaises(ValueError):
            parse_plan(response({**PLAN, 'next': ['b']}))


if __name__ == '__main__':
    unittest.main()
