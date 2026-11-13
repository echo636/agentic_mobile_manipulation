"""Every model-visible call declares intent before dispatch in the new protocol."""
from dataclasses import replace
import importlib.util
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from manipulation_agent.agent.vision_policy import system_prompt
from manipulation_agent.contracts import Budget
from manipulation_agent.observations.mock_rgb import MockRGBBackend
from manipulation_agent.records import Recorder
from manipulation_agent.runtime.vision_harness import VisionHarness
from manipulation_agent.tools import REGISTRY, tool_specs
from manipulation_agent.tools.intent import supervisor_finish_arguments


class IntentContract(unittest.TestCase):
    def make_harness(self, **kwargs):
        root = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'episode'
        recorder = Recorder(root, {'backend': 'mock'})
        backend = MockRGBBackend(root)
        harness = VisionHarness(backend, recorder, profile='skills',
                                require_tool_intent=True, **kwargs)
        return harness, backend, root

    def test_every_exposed_schema_requires_intent_without_mutating_legacy_registry(self):
        for profile in ('minimal', 'skills', 'workflow'):
            for tool in tool_specs(profile, require_intent=True):
                self.assertIn('intent', tool['inputSchema']['required'])
                self.assertEqual(tool['inputSchema']['properties']['intent']['type'], 'string')
                self.assertNotIn('intent', REGISTRY[tool['name']].schema['properties'])
        self.assertEqual(len(tool_specs('skills', require_intent=True)), 7)
        for tool in tool_specs('skills'):
            self.assertNotIn('intent', tool['inputSchema']['properties'])

    def test_missing_or_blank_intent_never_reaches_any_handler(self):
        harness, backend, root = self.make_harness()
        count = 0
        for name in harness.catalog:
            for fields in ({}, {'intent': ''}, {'intent': ' \n\t '}, {'intent': None},
                           {'intent': 42}, {'intent': 'x' * 601}):
                count += 1
                handler = Mock(return_value={})
                with self.subTest(tool=name, fields=fields), patch.dict(
                        REGISTRY, {name: replace(REGISTRY[name], handler=handler)}):
                    result = harness.call(name, fields, str(count))
                    self.assertFalse(result['ok'])
                    self.assertEqual(result['error']['code'], 'invalid_intent')
                    handler.assert_not_called()
        self.assertEqual(backend.steps, 0)
        self.assertFalse(harness.closed)
        calls = [json.loads(line) for line in (root / 'events.jsonl').read_text().splitlines()
                 if json.loads(line)['kind'] == 'tool_call']
        self.assertEqual(calls[0]['arguments'], {})

    def test_valid_intent_is_recorded_but_never_passed_to_motor_arguments(self):
        harness, backend, root = self.make_harness()
        initial = harness.call('initialize', {'intent': 'Inspect the prepared scene.'}, 'init')
        self.assertTrue(initial['ok'])
        intent = 'Turn on the selected device to meet the requested state.'
        arguments = {'primitive': 'toggle_on', 'revision': harness.revision,
                     'target': {'image_ref': harness.snapshot['images'][0]['image_ref'], 'point': [.5, .5]},
                     'intent': intent}
        execute = Mock(wraps=backend.execute_visual)
        backend.execute_visual = execute
        result = harness.call('act', arguments, 'act')
        self.assertTrue(result['ok'])
        self.assertEqual(harness.call('act', arguments, 'act'), result)
        self.assertEqual(execute.call_count, 1)
        self.assertNotIn('intent', execute.call_args.kwargs)
        events = [json.loads(line) for line in (root / 'events.jsonl').read_text().splitlines()]
        call = next(e for e in events if e['kind'] == 'tool_call' and e['request_id'] == 'act')
        self.assertEqual(call['arguments']['intent'], intent)
        receipt = harness.task_notes.read()['action_feedback_since_write']['entries'][-1]
        self.assertEqual(receipt['intent'], intent)
        self.assertEqual(harness.recorder.run['config']['tool_intent_protocol'], 'public_tool_intent_v1')

    def test_invalid_finish_does_not_cancel_or_close_the_episode(self):
        harness, backend, root = self.make_harness()
        bad = {'outcome': 'blocked', 'reason': 'Cannot proceed.', 'intent': '  '}
        harness.request_finish(bad, 'bad-finish')
        self.assertFalse(harness.deadline.stop_requested)
        self.assertFalse(harness.call('finish', bad, 'bad-finish')['ok'])
        self.assertFalse(harness.closed)
        good = {**bad, 'intent': 'End the episode and report the unresolved work.'}
        harness.request_finish(good, 'finish')
        self.assertTrue(harness.deadline.stop_requested)
        self.assertTrue(harness.call('finish', good, 'finish')['closed'])

    def test_notes_skill_reads_and_look_use_the_same_requirement(self):
        harness, backend, root = self.make_harness()
        cases = [
            ('list_skills', {'intent': 'Find guidance relevant to the upcoming manipulation.'}),
            ('read_skill', {'name': 'visual-manipulation', 'resource': 'SKILL.md',
                            'intent': 'Read the current pixel-selection and action protocol.'}),
            ('task_notes', {'operation': 'write', 'content': 'Inspect the device, then turn it on.',
                            'expected_version': 0, 'intent': 'Save the initial approach before moving.'}),
            ('task_notes', {'operation': 'read', 'content': None, 'expected_version': None,
                            'intent': 'Recover the recorded outstanding work.'}),
            ('look', {'yaw_degrees': 10, 'revision': 0,
                      'intent': 'Obtain a less occluded view of the target area.'}),
        ]
        for i, (name, args) in enumerate(cases):
            with self.subTest(tool=name):
                self.assertTrue(harness.call(name, args, str(i))['ok'])

    def test_rejected_motor_intent_preserves_existing_failed_attempt_budget_rule(self):
        harness, backend, root = self.make_harness(
            budget=Budget(wall_seconds=None), agent_step_limit=5)
        result = harness.call('act', {}, 'missing')
        self.assertFalse(result['ok'])
        self.assertEqual(harness.agent_steps, 1)
        self.assertEqual(backend.steps, 0)

    def test_supervisor_declares_its_own_closure_intent_and_is_labeled(self):
        harness, backend, root = self.make_harness()
        arguments = supervisor_finish_arguments('The step budget was exhausted.', require_intent=True)
        result = harness.call('finish', arguments, 'supervisor-finish', origin='supervisor')
        self.assertTrue(result['closed'])
        events = [json.loads(line) for line in (root / 'events.jsonl').read_text().splitlines()]
        call = next(e for e in events if e['kind'] == 'tool_call')
        self.assertEqual(call['origin'], 'supervisor')
        self.assertEqual(call['arguments']['intent'], arguments['intent'])
        self.assertNotIn('intent', supervisor_finish_arguments('Legacy cleanup.'))

    def test_both_arm_prompts_have_the_same_intent_instruction(self):
        a = system_prompt('skills', goal_skill='none', require_tool_intent=True)
        b = system_prompt('skills', goal_skill='hierarchical', require_tool_intent=True)
        phrase = 'Every tool call must include intent'
        self.assertIn(phrase, a)
        self.assertIn(phrase, b)
        self.assertNotIn('initialize({})', a)
        self.assertNotIn('initialize({})', b)


@unittest.skipUnless(importlib.util.find_spec('mcp'), 'Requires MCP SDK')
class IntentMCPProxy(unittest.IsolatedAsyncioTestCase):
    async def test_proxy_advertises_and_forwards_intent_without_rewriting_it(self):
        from manipulation_agent.mcp_server import create_server
        specs = tool_specs('skills', require_intent=True)
        forwarded = []
        def rpc(url, name, arguments, request_id, **kwargs):
            forwarded.append((name, arguments))
            return {'ok': True}
        with patch('manipulation_agent.mcp_server.fetch', return_value=json.dumps(
                {'ready': True, 'tools': specs}).encode()), \
                patch('manipulation_agent.mcp_server.rpc', side_effect=rpc):
            server = await create_server('http://127.0.0.1:1')
            for spec in specs:
                tool = server._tool_manager.get_tool(spec['name'])
                self.assertEqual(tool.parameters, spec['inputSchema'])
            await server._tool_manager.get_tool('initialize').fn(intent='Inspect the initial four views.')
        self.assertEqual(forwarded, [('initialize', {'intent': 'Inspect the initial four views.'})])


class IntentCLIBridge(unittest.TestCase):
    def test_cli_and_http_use_the_required_schema_before_actions_and_finish(self):
        from manipulation_agent.transport.bridge import rpc
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            output = root / 'episode'
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 0))
                port = probe.getsockname()[1]
            url = f'http://127.0.0.1:{port}'
            env = os.environ.copy()
            env['PYTHONPATH'] = str(repo / 'src')
            for name in ('MAS_EXECUTION_CLOCK_PATH', 'MAS_EPISODE_DEADLINE_UNIX'):
                env.pop(name, None)
            code = ('from manipulation_agent.transport import bridge; bridge.CLOSED_REPLAY_SECONDS=.01; '
                    'from manipulation_agent.vision_cli import main; raise SystemExit(main())')
            with (root / 'stderr.log').open('w+') as log:
                process = subprocess.Popen([sys.executable, '-c', code, '--backend', 'mock',
                    '--port', str(port), '--output', str(output), '--agent-profile', 'skills',
                    '--goal-skill', 'none', '--require-tool-intent',
                    '--max-agent-steps', '4'], cwd=repo, env=env, stdout=log, stderr=log)
                try:
                    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                    until = time.monotonic() + 15
                    while True:
                        if process.poll() is not None:
                            log.seek(0)
                            self.fail('Mock CLI exited: ' + log.read())
                        try:
                            with opener.open(url + '/healthz', timeout=1) as response:
                                health = json.load(response)
                            break
                        except OSError:
                            if time.monotonic() >= until:
                                self.fail('Mock bridge startup timed out')
                            time.sleep(.03)
                    self.assertEqual(len(health['tools']), 7)
                    self.assertTrue(all('intent' in t['inputSchema']['required'] for t in health['tools']))
                    self.assertEqual(rpc(url, 'initialize', {}, 'missing', timeout=2)['error']['code'],
                                     'invalid_intent')
                    initial = rpc(url, 'initialize', {'intent': 'Inspect the initial scene.'}, 'init', timeout=2)
                    rpc(url, 'task_notes', {'operation': 'write', 'content': 'Turn on the selected device.',
                        'expected_version': 0, 'intent': 'Record the intended task approach.'}, 'notes', timeout=2)
                    args = {'primitive': 'toggle_on', 'revision': initial['observation']['revision'],
                            'target': {'image_ref': initial['observation']['images'][0]['image_ref'],
                                       'point': [.5, .5]}, 'intent': 'Turn on the visible target device.'}
                    self.assertTrue(rpc(url, 'act', args, 'act', timeout=2)['ok'])
                    bad = {'outcome': 'achieved', 'reason': 'Visible result.', 'intent': ' '}
                    self.assertFalse(rpc(url, 'finish', bad, 'bad-finish', timeout=2)['ok'])
                    self.assertTrue(rpc(url, 'finish', {**bad, 'intent': 'Close after checking the visible result.'},
                                        'finish', timeout=2)['closed'])
                    process.wait(timeout=5)
                    self.assertEqual(process.returncode, 0)
                    run = json.loads((output / 'run.json').read_text())
                    self.assertEqual(run['config']['tool_intent_protocol'], 'public_tool_intent_v1')
                    self.assertTrue(run['config']['require_tool_intent'])
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try: process.wait(timeout=5)
                        except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
