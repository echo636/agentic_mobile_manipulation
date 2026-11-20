import base64
import copy
import json
from pathlib import Path
import tempfile
import unittest

from manipulation_agent.agent_loop import LoopConfig, VisualToolLoop
from manipulation_agent.image_history import ImageHistory
from manipulation_agent.observations.mock_rgb import MockRGBBackend
from manipulation_agent.records import Recorder
from manipulation_agent.vision_harness import VisionHarness


class NativeLoopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'episode'
        self.recorder = Recorder(self.path, {'backend': 'mock', 'observation_mode': 'rgb_only'})
        self.backend = MockRGBBackend(self.path)
        self.harness = VisionHarness(self.backend, self.recorder, profile='minimal')

    def response(self, name, args, ident):
        return {'output': [{'type': 'function_call', 'call_id': ident,
                            'name': name, 'arguments': json.dumps(args)}], 'usage': {'total_tokens': 10}}

    def test_failed_action_new_rgb_recovery_and_finish_keep_real_feedback(self):
        requests = []
        def request(harness, payload):
            requests.append(copy.deepcopy(payload))
            turn = len(requests)
            if turn == 1:
                return self.response('initialize', {}, 'initialize')
            if turn in (2, 3):
                blocks = payload['input'][-1]['content']
                pictures = [b for b in blocks if b['type'] == 'input_image']
                self.assertEqual(len(pictures), 4)
                if turn == 2:
                    self.assertEqual(harness.backend.capture, 1)
                    self.assertEqual({t['name'] for t in payload['tools']},
                                     {'initialize', 'look', 'act', 'finish'})
                self.assertTrue(base64.b64decode(pictures[0]['image_url'].split(',')[1]).startswith(b'\x89PNG'))
                self.assertNotIn('PRIVATE_OBJECT_NAME', json.dumps(payload))
                if turn == 2:
                    self.backend.fail_next = True
                else:
                    feedback = next(i for i in reversed(payload['input']) if i.get('type') == 'function_call_output')
                    result = json.loads(feedback['output'])
                    self.assertFalse(result['ok'])
                    self.assertEqual(result['observation']['revision'], 1)
                return self.response('act', {'primitive': 'toggle_on', 'revision': harness.revision,
                                      'target': {'image_ref': harness.snapshot['images'][0]['image_ref'],
                                                 'point': [.5, .5]}}, f'action-{turn}')
            return self.response('finish', {'outcome': 'achieved', 'reason': 'Mock indicator changed'}, 'finish')
        VisualToolLoop(model='fixture', instructions='fixture', request=request,
                       config=LoopConfig(image_history_captures=1)).run(self.harness, 'fixture')
        self.assertTrue(self.recorder.run['task_success'])
        self.assertEqual(self.backend.steps, 2)
        events = [json.loads(line) for line in (self.path / 'events.jsonl').read_text().splitlines()]
        inputs = [event for event in events if event['kind'] == 'model_request']
        self.assertEqual([len(e['input_captures']) for e in inputs], [0, 1, 1, 1])
        self.assertEqual(inputs[-1]['historical_captures_omitted'], 2)
        self.assertTrue((self.path / 'model_events.compat.jsonl').exists())

    def test_image_retention_does_not_rewrite_history_or_lose_latest_capture(self):
        ledger = ImageHistory(1)
        history = []
        first = self.backend.observe()
        first['revision'] = 0
        ledger.append(history, first, self.backend.image_bytes)
        second = self.backend.observe()
        second['revision'] = 1
        ledger.append(history, second, self.backend.image_bytes)
        original = copy.deepcopy(history)
        outbound, visible, omitted = ledger.outbound(history)
        self.assertEqual(history, original)
        self.assertEqual(omitted, 1)
        self.assertEqual(visible[0]['capture'], second['capture'])
        self.assertNotIn('input_image', json.dumps(outbound[0]))
        self.assertEqual(sum(b['type'] == 'input_image' for b in outbound[1]['content']), 4)
        corrupt = copy.deepcopy(second)
        corrupt['images'][0]['sha256'] = 'bad'
        with self.assertRaises(RuntimeError):
            ledger.append(history, corrupt, self.backend.image_bytes)

    def test_budget_stops_text_only_model_without_claiming_success(self):
        calls = []
        def request(harness, payload):
            calls.append(payload)
            return {'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'Done'}]}],
                    'usage': {'total_tokens': 25}}
        VisualToolLoop(model='fixture', instructions='fixture', request=request,
                       config=LoopConfig(max_tokens=20)).run(self.harness, 'fixture')
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.recorder.run['agent_outcome'], 'aborted')
        self.assertFalse(self.recorder.run['task_success'])
        self.assertEqual(self.recorder.run['finish_reason'], 'model_token_budget_exhausted')

    def test_public_returned_summary_retained_without_opaque_reasoning(self):
        def request(harness, payload):
            response = self.response('finish', {'outcome': 'blocked', 'reason': 'fixture'}, 'finish')
            response['output'].insert(0, {'type': 'reasoning', 'encrypted_content': 'OPAQUE_PRIVATE',
                                        'summary': [{'type': 'summary_text', 'text': 'Returned public summary'}]})
            return response
        VisualToolLoop(model='fixture', instructions='fixture', request=request).run(self.harness, 'fixture')
        events = (self.path / 'events.jsonl').read_text()
        compat = (self.path / 'model_events.compat.jsonl').read_text()
        self.assertNotIn('OPAQUE_PRIVATE', events + compat)
        self.assertIn('Returned public summary', events)
        self.assertIn('Returned public summary', compat)

    def test_native_records_render_with_model_summary_and_call_identity(self):
        from manipulation_agent.replay import render_replay
        def request(harness, payload):
            response = self.response('finish', {'outcome':'blocked','reason':'fixture'}, 'finish-call')
            response['output'].insert(0, {'type':'reasoning', 'encrypted_content':'DO_NOT_EXPORT',
                'summary':[{'type':'summary_text','text':'Public summary from provider'}]})
            response['output'].insert(1, {'type':'message','id':'msg-1','content':[
                {'type':'output_text','text':'Recorded assistant output'}]})
            return response
        VisualToolLoop(model='native-model', instructions='fixture', request=request).run(self.harness,'fixture')
        replay=render_replay(self.path)
        self.assertEqual(replay['model'],'native-model')
        self.assertTrue(replay['has_public_trace'])
        self.assertEqual(replay['reasoning_availability'],'provider_returned_summary')
        self.assertEqual(len(replay['model_reasoning_summaries']),1)
        timeline=replay['model_transcript']
        self.assertEqual(sum(e['kind']=='provider_summary' for e in timeline),1)
        call=next(e for e in timeline if e['kind']=='tool_call')
        self.assertEqual((call['call_id'],call['step']),('finish-call',1))
        self.assertEqual(replay['steps'][0]['request_id'],'finish-call')
        self.assertTrue((self.path/'replay.html').exists())
        self.assertTrue((self.path/'model_public_events.jsonl').exists())
        canonical=[json.loads(row) for row in (self.path/'canonical_events.jsonl').read_text().splitlines()]
        self.assertEqual([e['kind'] for e in canonical],
                         ['usage','reasoning_summary','assistant_text','tool_call','tool_result','run_finished'])
        self.assertTrue(all(e['client']=='responses' and e['turn']==0 for e in canonical))
        self.assertNotIn('DO_NOT_EXPORT',json.dumps(canonical)+json.dumps(replay))

    def test_expired_model_network_timeout_closes_episode_as_timeout(self):
        from unittest.mock import patch
        from manipulation_agent.deadline import EpisodeDeadline
        from manipulation_agent.vision_policy import RGBResponsesPolicy
        self.harness.deadline=EpisodeDeadline(100)
        self.backend.deadline=self.harness.deadline
        policy=RGBResponsesPolicy(model='fixture',base_url='http://127.0.0.1',key='fixture')
        clock=[99.0]
        def timeout(*args,**kwargs):
            clock[0]=101.0
            raise TimeoutError('fixture network timeout')
        with patch('manipulation_agent.deadline.time.time',side_effect=lambda:clock[0]), \
             patch('manipulation_agent.policies.urllib.request.urlopen',side_effect=timeout):
            policy.run(self.harness,'fixture')
        self.assertEqual(self.recorder.run['episode_outcome'],'timeout')
        self.assertEqual(self.recorder.run['finish_reason'],'episode_deadline_exceeded')
        self.assertTrue(self.harness.closed)

    def test_independent_network_timeout_remains_a_network_error(self):
        from unittest.mock import patch
        from manipulation_agent.deadline import EpisodeDeadline
        from manipulation_agent.policies import ResponsesPolicy
        policy=ResponsesPolicy(model='fixture',base_url='http://127.0.0.1',key='fixture')
        with patch('manipulation_agent.deadline.time.time',return_value=99), \
             patch('manipulation_agent.policies.urllib.request.urlopen',side_effect=TimeoutError('network')):
            with self.assertRaises(TimeoutError):policy._request({},deadline=EpisodeDeadline(200))
