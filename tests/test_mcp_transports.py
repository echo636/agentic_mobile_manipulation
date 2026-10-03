"""Real SDK clients/transports + the real queued bridge with a CPU RGB fixture.

These tests validate wire contracts, not OmniGibson execution or model quality.
"""
import argparse
import asyncio
import base64
from contextlib import asynccontextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

from manipulation_agent.mcp_server import loopback_host
from manipulation_agent.tools import tool_specs

HAS_MCP = importlib.util.find_spec('mcp') is not None
ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


@unittest.skipUnless(HAS_MCP, 'Install the pinned [mcp] extra for wire integration tests')
class MCPTransports(unittest.IsolatedAsyncioTestCase):
    profile = 'workflow'
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / 'episode'
        self.env = os.environ.copy()
        self.env['PYTHONPATH'] = str(ROOT / 'src')
        for name in ('MAS_EXECUTION_CLOCK_PATH', 'MAS_EPISODE_DEADLINE_UNIX'):
            self.env.pop(name, None)
        self.port = free_port()
        self.url = f'http://127.0.0.1:{self.port}'
        self.errors = open(Path(self.temp.name) / 'processes.log', 'w+')
        self.addCleanup(self.errors.close)
        # Real main-thread serve loop. Only its post-close cache window is short.
        code = ('from manipulation_agent import bridge; bridge.CLOSED_REPLAY_SECONDS=0.1; '
                'from manipulation_agent.vision_cli import main; raise SystemExit(main())')
        self.backend = subprocess.Popen([sys.executable, '-c', code, '--backend', 'mock',
            '--port', str(self.port), '--output', str(self.output), '--agent-profile', self.profile],
            cwd=ROOT, env=self.env, stdout=subprocess.DEVNULL, stderr=self.errors)
        self.addCleanup(self.stop, self.backend)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.backend.poll() is not None:
                self.fail('Mock backend exited before readiness; see process log')
            try:
                with opener.open(self.url + '/healthz', timeout=.2) as response:
                    self.health = json.load(response)
                    break
            except OSError:
                time.sleep(.03)
        else:
            self.fail('Mock backend did not become ready')

    @staticmethod
    def stop(process):
        if process.poll() is None:
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=5)

    @asynccontextmanager
    async def client(self, transport):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        from mcp.client.sse import sse_client
        from mcp.client.streamable_http import streamable_http_client
        import httpx
        argv = ['-m', 'manipulation_agent.mcp_server', '--bridge', self.url, '--transport', transport]
        if transport == 'stdio':
            async with stdio_client(StdioServerParameters(command=sys.executable, args=argv,
                                                          env=self.env, cwd=ROOT), errlog=self.errors) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize()
                    yield session
            return
        port = free_port()
        process = subprocess.Popen([sys.executable, *argv, '--port', str(port)], cwd=ROOT,
                                   env=self.env, stdout=subprocess.DEVNULL, stderr=self.errors)
        self.addCleanup(self.stop, process)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if process.poll() is not None: self.fail('FastMCP HTTP process exited')
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=.1): break
            except OSError: await asyncio.sleep(.03)
        else: self.fail('FastMCP HTTP did not listen')
        if transport == 'sse':
            def factory(headers=None, timeout=None, auth=None):
                return httpx.AsyncClient(headers=headers, timeout=timeout, auth=auth, trust_env=False)
            async with sse_client(f'http://127.0.0.1:{port}/sse', httpx_client_factory=factory) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize()
                    yield session
        else:
            async with httpx.AsyncClient(trust_env=False) as http:
                async with streamable_http_client(f'http://127.0.0.1:{port}/mcp', http_client=http) as streams:
                    async with ClientSession(*streams[:2]) as session:
                        await session.initialize()
                        yield session

    def result(self, response):
        self.assertFalse(response.isError)
        return json.loads(response.content[0].text)

    def check_images(self, response, body):
        metadata = body['observation']['images']
        images = [part for part in response.content if part.type == 'image']
        self.assertEqual(len(images), 4)
        self.assertEqual({x['view'] for x in metadata}, {'front', 'back', 'left', 'right'})
        hashes = []
        for frame, image in zip(metadata, images):
            raw = base64.b64decode(image.data)
            self.assertTrue(raw.startswith(b'\x89PNG\r\n\x1a\n'))
            self.assertEqual(struct.unpack('!II', raw[16:24]), (96, 64))
            self.assertEqual(image.mimeType, frame['mime_type'])
            self.assertEqual(hashlib.sha256(raw).hexdigest(), frame['sha256'])
            hashes.append(frame['sha256'])
        return hashes

    async def exercise(self, transport):
        self.assertEqual(self.health['rpc_timeout_seconds'], 1920 if self.profile == 'official' else 300)
        async with self.client(transport) as session:
            listed = await session.list_tools()
            self.assertEqual({t.name: t.inputSchema for t in listed.tools},
                             {t['name']: t['inputSchema'] for t in tool_specs(self.profile)})
            observe_spec = next(t for t in listed.tools if t.name == 'observe')
            self.assertTrue(observe_spec.annotations.readOnlyHint)
            self.assertFalse(observe_spec.annotations.idempotentHint)
            self.assertFalse(observe_spec.meta['mas']['mutates_world'])
            observed = await session.call_tool('observe', {})
            observation = self.result(observed)
            before = self.check_images(observed, observation)
            target = {'image_ref': observation['observation']['images'][0]['image_ref'], 'point': [.5, .5]}
            args = {'primitive': 'toggle_on', 'target': target, 'revision': 0}
            # Owner-thread validation must see booleans/strings/extras unchanged.
            bad = await session.call_tool('act', {**args, 'revision': True})
            self.assertEqual(self.result(bad)['error']['code'], 'invalid_arguments')
            bad_extra = await session.call_tool('observe', {'unexpected': 'preserve me'})
            self.assertEqual(self.result(bad_extra)['error']['code'], 'invalid_arguments')
            # Archived clients can omit nullable options; exact advertised schema
            # is still unchanged and the harness owns compatibility defaults.
            acted = await session.call_tool('act', args)
            action = self.result(acted)
            self.assertTrue(action['ok'])
            self.assertEqual(action['effect']['verification'], 'executor_operation_only')
            self.assertNotEqual(before, self.check_images(acted, action))
            self.assertNotIn('PRIVATE_OBJECT_NAME', json.dumps(action))
            finished = self.result(await session.call_tool('finish', {'outcome': 'achieved', 'reason': 'Green RGB fixture'}))
            self.assertTrue(finished['closed'])
            self.assertNotIn('evaluation', finished)
            evidence_id = action['evidence_id']
        await asyncio.to_thread(self.backend.wait, 5)
        self.assertEqual(self.backend.returncode, 0)
        run = json.loads((self.output / 'run.json').read_text())
        self.assertTrue(run['task_success'])
        events = [json.loads(line) for line in (self.output / 'events.jsonl').read_text().splitlines()]
        self.assertTrue(any(e.get('id') == evidence_id and e.get('kind') == 'tool_result' for e in events))
        self.assertTrue(any(e.get('kind') == 'tool_call' and e.get('arguments', {}).get('revision') is True for e in events))
        self.assertTrue(any(e.get('kind') == 'tool_call' and e.get('arguments', {}).get('unexpected') == 'preserve me' for e in events))

    async def test_stdio_rgb_evidence_and_exact_schema(self):
        await asyncio.wait_for(self.exercise('stdio'), 30)

    async def test_sse_rgb_evidence_and_exact_schema(self):
        await asyncio.wait_for(self.exercise('sse'), 30)

    async def test_streamable_http_rgb_evidence_and_exact_schema(self):
        await asyncio.wait_for(self.exercise('streamable-http'), 30)

    def test_existing_readonly_preflight_accepts_fastmcp(self):
        from manipulation_agent.mcp_preflight import check_server
        result = check_server(sys.executable, ['-m', 'manipulation_agent.mcp_server', '--bridge', self.url],
                              tool_specs(self.profile), Path(self.temp.name), timeout=10)
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['tool_calls'], 0)


class OfficialMCPTransports(MCPTransports):
    """The Official catalog keeps its fourteen-primitive schema on every wire."""
    profile = 'official'


class LoopbackBinding(unittest.TestCase):
    def test_loopback_binding_only(self):
        for host in ('127.0.0.1', '::1', 'localhost'):
            self.assertEqual(loopback_host(host), host)
        for host in ('0.0.0.0', '::', '10.76.5.241', 'example.com'):
            with self.assertRaises(argparse.ArgumentTypeError): loopback_host(host)


class ToolContextBinding(unittest.TestCase):
    def test_context_tracks_request_and_episode_without_changing_handler_contract(self):
        from dataclasses import replace
        from unittest.mock import patch
        from manipulation_agent.observations.mock_rgb import MockRGBBackend
        from manipulation_agent.records import Recorder
        from manipulation_agent.tool_context import ToolContext
        from manipulation_agent.tools import REGISTRY
        from manipulation_agent.vision_harness import VisionHarness
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'episode'
            harness = VisionHarness(MockRGBBackend(output), Recorder(output, {}), profile='minimal')
            contexts = []
            original = REGISTRY['observe']
            def capture(context):
                contexts.append(context)
                return original.handler(context)
            with patch.dict(REGISTRY, observe=replace(original, handler=capture)):
                first = harness.call('observe', {}, 'request-one')
                second = harness.call('observe', {}, 'request-two')
            self.assertTrue(first['ok'] and second['ok'])
            self.assertTrue(all(isinstance(ctx, ToolContext) for ctx in contexts))
            self.assertEqual([ctx.request_id for ctx in contexts], ['request-one', 'request-two'])
            self.assertEqual(contexts[0].tool_name, 'observe')
            self.assertEqual(contexts[0].profile, 'minimal')
            harness.call('act', {'primitive': 'release', 'target': None, 'revision': 0}, 'move')
            self.assertEqual(contexts[0].revision, 1)


if __name__ == '__main__': unittest.main()
