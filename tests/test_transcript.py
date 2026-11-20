import json
import unittest

from manipulation_agent.transcript import build_transcript


def event(kind, ident, state='completed', **fields):
    return {'type': 'item.' + state, 'item': {'type': kind, 'id': ident, **fields}}


class TranscriptTests(unittest.TestCase):
    def test_interleaved_messages_final_text_and_all_result_blocks_keep_order(self):
        call = dict(tool='observe', arguments={})
        events = [event('agent_message', 'a', text='First\nOriginal <text>'),
                  event('mcp_tool_call', 'c', 'started', **call),
                  event('agent_message', 'b', text='While tool runs'),
                  event('mcp_tool_call', 'c', **call, result={'content': [
                      {'type': 'text', 'text': 'first block'}, {'type': 'text', 'text': 'second block'},
                      {'type': 'image', 'data': 'DO_NOT_INLINE_IMAGE'}]}),
                  event('agent_message', 'z', text='Final output')]
        steps = [{'index': 1, 'tool': 'observe', 'arguments': {}, 'model_call_event_id': 'c'}]
        out = build_transcript(events, steps, [])
        self.assertEqual([e['kind'] for e in out], ['assistant', 'tool_call', 'assistant', 'tool_result', 'assistant'])
        self.assertEqual([e['source_event_index'] for e in out], list(range(5)))
        self.assertEqual(out[0]['text'], 'First\nOriginal <text>')
        self.assertEqual(out[3]['text_blocks'], ['first block', 'second block'])
        self.assertEqual(out[3]['image_count'], 1)
        self.assertIsNone(out[-1]['step'])
        self.assertNotIn('DO_NOT_INLINE_IMAGE', json.dumps(out))

    def test_transport_failure_and_unfinished_call_are_not_lost(self):
        events = [event('mcp_tool_call', 'c', tool='act', arguments={}, error={'message': 'disconnected'}),
                  event('agent_message', 'a', text='Retry'),
                  event('mcp_tool_call', 'd', 'started', tool='act', arguments={})]
        out = build_transcript(events, [], [])
        self.assertEqual([e['kind'] for e in out], ['tool_call', 'tool_result', 'assistant', 'tool_call'])
        self.assertEqual(out[1]['error'], {'message': 'disconnected'})
        self.assertTrue(all(e['step'] is None for e in out))

    def test_public_summary_text_is_preserved_without_opaque_content(self):
        events = [event('reasoning', 'r', text='Returned summary', encrypted_content='OPAQUE'),
                  event('reasoning', 'opaque', encrypted_content='OPAQUE')]
        summary = {'text': 'Returned summary', 'at': '2026-10-01T00:00:00Z'}
        out = build_transcript(events, [], [summary])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['kind'], 'provider_summary')
        self.assertEqual(out[0]['text'], summary['text'])
        self.assertNotIn('OPAQUE', json.dumps(out))

    def test_evidence_ids_align_retry_after_transport_failure(self):
        steps = [{'index': 1, 'tool': 'observe', 'arguments': {},
                  'model_call_event_id': 'failed', 'result_event_id': 'e1'}]
        events = [event('mcp_tool_call', 'failed', tool='observe', arguments={}, error={'message': 'offline'}),
                  event('mcp_tool_call', 'retry', tool='observe', arguments={},
                        result={'content': [{'type': 'text', 'text': '{"evidence_id":"e1"}'}]})]
        out = build_transcript(events, steps, [])
        self.assertEqual([e['step'] for e in out], [None, None, 1, 1])

    def test_separate_summary_clock_does_not_reorder_public_events(self):
        one = {'text': 'One', 'at': '2026-10-01T00:00:00Z'}
        two = {'text': 'Two', 'at': '2026-10-01T00:00:01Z'}
        steps = [{'index': 1, 'tool': 'observe', 'arguments': {}, 'model_call_event_id': 'c',
                  'model_reasoning_summaries': [one, two]}]
        events = [event('agent_message', 'a', text='Message'),
                  event('mcp_tool_call', 'c', tool='observe', arguments={})]
        out = build_transcript(events, steps, [one, two])
        self.assertEqual([e['text'] for e in out if 'text' in e], ['One', 'Two', 'Message'])
        self.assertEqual(out[0]['alignment'], 'next_tool_by_recorded_timestamp')
        self.assertEqual([e['source_event_index'] for e in out if 'source_event_index' in e], [0, 1, 1])


if __name__ == '__main__':
    unittest.main()
