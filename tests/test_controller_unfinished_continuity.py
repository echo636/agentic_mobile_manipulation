"""A finished client turn must not discard an unfinished robot conversation."""
import importlib.util
import json
from pathlib import Path
import time
import pytest

from manipulation_agent.clients.codex import CodexAdapter
from manipulation_agent.clients.types import ClientCapability, ClientRunResult


@pytest.mark.parametrize('first_closes,second_closes,mode', [
    (False, True, 'same_thread'), (True, True, 'same_thread'),
    (False, False, 'same_thread'), (False, True, 'fresh_thread')])
def test_unfinished_turn_resumes_original_thread_instead_of_starting_over(
        tmp_path, monkeypatch, first_closes, second_closes, mode):
    spec = importlib.util.spec_from_file_location('unfinished_controller',
        Path(__file__).parents[1] / 'scripts/run_codex_controller.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    thread = '01a1720e-15b3-78e0-b164-81d3f6911bf4'

    class Adapter(CodexAdapter):
        prepared = 0
        resumed = []
        runs = 0

        def probe(self):
            return ClientCapability('codex', 'available', 'fixture')

        def prepare_project(self, *args):
            self.prepared += 1
            return super().prepare_project(*args)

        def prepare_resume(self, project, thread_id, prompt):
            self.resumed.append(thread_id)
            return super().prepare_resume(project, thread_id, prompt)

        def run(self, project, output, timeout, on_start=None):
            self.runs += 1
            rows = [{'type': 'thread.started', 'thread_id': thread}]
            if not (first_closes if self.runs == 1 else second_closes):
                rows.append({'type': 'item.completed', 'item': {
                    'id': 'item_1', 'type': 'agent_message',
                    'text': 'Task unfinished; I need to continue.'}})
            else:
                rows.append({'type': 'item.completed', 'item': {
                    'id': 'item_1', 'type': 'mcp_tool_call', 'server': 'manipulation',
                    'tool': 'finish', 'arguments': {'outcome': 'blocked'},
                    'result': {'content': [{'type': 'text',
                        'text': json.dumps({'ok': True, 'closed': True})}]}}})
            (output / 'model_events.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
            return ClientRunResult(0, False, time.time(), .01, 123)

    adapter = Adapter()
    monkeypatch.setattr(module, 'get_adapter', lambda _: adapter)
    monkeypatch.setattr(module, 'check_server', lambda *args, **kwargs: {'status': 'passed'})
    monkeypatch.setattr(module, 'export_summaries', lambda *args, **kwargs: {'status': 'fixture'})
    output = tmp_path / 'controller'
    argv = ['--model', 'fixture', '--instruction', 'Continue the robot task',
            '--mcp-command', 'unused', '--mcp-args-json', '[]', '--output', str(output),
            '--unfinished-restarts', '1', '--unfinished-recovery', mode]
    assert module.main(argv) == (0 if first_closes or second_closes else 2)
    assert adapter.runs == (1 if first_closes else 2)
    record = json.loads((output / 'controller.json').read_text())
    assert record['formal_finish_observed'] == (first_closes or second_closes)
    if not first_closes and mode == 'same_thread':
        assert adapter.prepared == 1  # The old fresh-thread path prepares twice.
        assert adapter.resumed == [thread]
        assert record['unfinished_thread_continuations'][0]['thread_id'] == thread
        assert not record.get('fresh_thread_restarts')
    else:
        assert adapter.resumed == []
        assert adapter.prepared == (1 if first_closes else 2)
