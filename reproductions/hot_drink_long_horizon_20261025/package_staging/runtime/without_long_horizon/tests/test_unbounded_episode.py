"""No episode wall timeout while action and step budgets remain active."""
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

from manipulation_agent.contracts import Budget


def test_budget_allows_only_wall_limit_to_be_disabled():
    budget = Budget(wall_seconds=None, max_actions=120, max_sim_steps=20000)
    assert budget.wall_seconds is None
    assert budget.max_actions == 120
    assert budget.max_sim_steps == 20000


def test_controller_unbounded_wait_still_records_formal_finish(tmp_path):
    path = Path(__file__).resolve().parents[1] / 'scripts/run_codex_controller.py'
    spec = importlib.util.spec_from_file_location('unbounded_controller', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    waits = []

    class Process:
        pid = 123456
        returncode = 0

        def __init__(self, *args, **kwargs):
            self.stdout = kwargs['stdout']

        def communicate(self, text, timeout):
            waits.append(timeout)
            self.stdout.write(json.dumps({'type': 'item.completed', 'item': {
                'type': 'mcp_tool_call', 'server': 'manipulation', 'tool': 'finish',
                'result': {'content': [{'type': 'text', 'text': '{"closed":true}'}]}}}) + '\n')
            return None, None

    output = tmp_path / 'controller'
    argv = ['--model', 'fixture', '--instruction', 'fixture', '--mcp-command', 'fixture',
            '--mcp-args-json', '[]', '--output', str(output), '--unbounded-episode']
    with patch.object(module, 'check_server', return_value={'status': 'passed'}), \
         patch.object(module.subprocess, 'Popen', Process), \
         patch.object(module.subprocess, 'check_output', return_value='fixture'), \
         patch.object(module, 'source_version', return_value={'fixture': True}), \
         patch.object(module, 'export_summaries', return_value={}):
        assert module.main(argv) == 0
    record = json.loads((output / 'controller.json').read_text())
    assert waits == [None]
    assert record['effective_timeout_seconds'] is None
    assert record['formal_finish_observed'] is True


def test_mock_bridge_unbounded_episode_obeys_action_budget(tmp_path):
    """Exercise CLI -> bridge -> harness, including startup clock and closure."""
    import os
    import socket
    import subprocess
    import sys
    import time
    from manipulation_agent.bridge import rpc
    from urllib.request import ProxyHandler, build_opener

    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    output = tmp_path / 'run'
    env = os.environ.copy()
    env.pop('MAS_EXECUTION_CLOCK_PATH', None)
    env.pop('MAS_EPISODE_DEADLINE_UNIX', None)
    cmd = [sys.executable, '-m', 'manipulation_agent.vision_cli', '--backend', 'mock',
           '--policy', 'serve', '--agent-profile', 'minimal', '--port', str(port),
           '--output', str(output), '--max-actions', '1', '--unbounded-episode']
    with (tmp_path / 'cli.log').open('w') as log:
        process = subprocess.Popen(cmd, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            url = f'http://127.0.0.1:{port}'
            opener = build_opener(ProxyHandler({}))
            for _ in range(100):
                assert process.poll() is None, (tmp_path / 'cli.log').read_text()
                try:
                    with opener.open(url + '/healthz', timeout=.2) as response:
                        state = json.load(response)
                    break
                except OSError:
                    time.sleep(.05)
            else:
                raise AssertionError('Bridge did not become ready')
            assert state['ready']
            initial = rpc(url, 'initialize', {}, 'init')
            assert initial['ok']
            action = rpc(url, 'act', {'primitive': 'toggle_on',
                'revision': initial['observation']['revision'],
                'target': {'image_ref': initial['observation']['images'][0]['image_ref'], 'point': [.5,.5]}}, 'act')
            assert action['actions_remaining'] == 0
            with opener.open(url + '/healthz') as response:
                assert json.load(response)['budget_exhausted'] is True
            final = rpc(url, 'finish', {'outcome':'achieved','reason':'Fixture radio turned on'}, 'finish')
            assert final['closed'] is True
            record = json.loads((output / 'run.json').read_text())
            assert record['budget']['wall_seconds'] is None
            assert record['task_success'] is True
            assert record['config']['episode_deadline_unix'] is None
        finally:
            process.terminate()
            process.wait(timeout=10)
