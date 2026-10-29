"""Codex restarts concatenate CLI streams whose item IDs can repeat."""
import json
from pathlib import Path
import tempfile
import unittest

from manipulation_agent.clients.events import parse_events


def tool_events(name, item_id='item_0'):
    item = {'id': item_id, 'type': 'mcp_tool_call', 'server': 'manipulation',
            'tool': name, 'arguments': {'primitive': 'wait'} if name == 'act' else {}}
    return [{'type': 'item.started', 'item': item},
            {'type': 'item.completed', 'item': {**item, 'result': {'content': []}}}]


class CodexRestartEventTests(unittest.TestCase):
    def parse(self, rows):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'model_events.jsonl'
            path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
            return parse_events(path, 'codex')

    def assert_two_distinct_calls(self, parsed):
        calls = [row for row in parsed.canonical if row['kind'] == 'tool_call']
        results = [row for row in parsed.canonical if row['kind'] == 'tool_result']
        self.assertEqual([row['tool'] for row in calls], ['act', 'finish'])
        self.assertEqual([row['tool'] for row in results], ['act', 'finish'])
        self.assertEqual(len({row['call_id'] for row in calls}), 2)
        self.assertEqual([row['call_id'] for row in calls], [row['call_id'] for row in results])

    def test_fresh_threads_reusing_item_id_preserve_both_calls(self):
        rows = [{'type': 'thread.started', 'thread_id': 'first'}, *tool_events('act'),
                {'type': 'thread.started', 'thread_id': 'second'}, *tool_events('finish')]
        self.assert_two_distinct_calls(self.parse(rows))

    def test_resumed_cli_stream_with_same_thread_id_is_a_new_id_namespace(self):
        rows = [{'type': 'thread.started', 'thread_id': 'same-thread'}, *tool_events('act'),
                {'type': 'thread.started', 'thread_id': 'same-thread'}, *tool_events('finish')]
        self.assert_two_distinct_calls(self.parse(rows))

    def test_missing_first_header_does_not_collide_with_later_cli_stream(self):
        rows = [*tool_events('act'), {'type': 'thread.started', 'thread_id': 'later'},
                *tool_events('finish')]
        self.assert_two_distinct_calls(self.parse(rows))


if __name__ == '__main__':
    unittest.main()
