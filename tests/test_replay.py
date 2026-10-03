import base64
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from manipulation_agent.audit import audit_episode, audit_rgb_transport
from manipulation_agent.replay import build_replay, render_replay


class ReplayEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root/'frames').mkdir()
        self.payload = b'fixture image bytes'
        (self.root/'frames/img.jpg').write_bytes(self.payload)
        sha = hashlib.sha256(self.payload).hexdigest()
        meta = {'image_ref':'img','view':'head','width':512,'height':512,'mime_type':'image/jpeg','sha256':sha}
        self.obs = {'observation_mode':'rgb_only','revision':0,'images':[meta]}
        (self.root/'captures.jsonl').write_text(json.dumps({'env_steps':0,'images':[{'image_ref':'img','file':'frames/img.jpg','sha256':sha}]})+'\n')
        run = {'run_id':'test','status':'passed','config':{'observation_mode':'rgb_only','instruction':'</script><script>alert(1)</script>'}}
        (self.root/'run.json').write_text(json.dumps(run))
        def event(kind, id, **kw):
            return {'kind':kind,'id':id,'at':'2026-09-30T00:00:00+00:00',**kw}
        self.events = [event('episode_started','initial',observation=self.obs),
                       event('private_executor_result','private',details={'secret':'HIDDEN_WORLD_POSE'}),
                       event('tool_call','call1',request_id='1',name='observe',arguments={}),
                       event('tool_result','res1',request_id='1',name='observe',result={'ok':True,'observation':self.obs}),
                       event('tool_call','call2',request_id='2',name='act',arguments={'primitive':'toggle_on','revision':0,'target':{'image_ref':'img','point':[.4,.6]}}),
                       event('tool_result','res2',request_id='2',name='act',result={'ok':False,'error':{'code':'out_of_reach'},'observation':self.obs})]
        self.models=[]
        for call,result in zip(self.events[2::2], self.events[3::2]):
            content=[{'type':'text','text':json.dumps({**result['result'],'evidence_id':result['id']})},
                     {'type':'image','data':base64.b64encode(self.payload).decode(),'mimeType':'image/jpeg'}]
            self.models.append({'type':'item.completed','item':{'type':'mcp_tool_call','tool':call['name'],'arguments':call['arguments'],'result':{'content':content}}})
        self.save()

    def save(self):
        (self.root/'events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in self.events))

    def test_first_observation_is_not_leaked_from_constructor(self):
        data=build_replay(self.root)
        self.assertIsNone(data['steps'][0]['before'])
        self.assertEqual(data['steps'][1]['before'],data['steps'][0]['after'])
        self.assertEqual(data['steps'][1]['status'],'failed')
        self.assertNotIn('HIDDEN_WORLD_POSE',json.dumps(data))

    def test_native_crash_uses_termination_overlay_without_rewriting_raw_run(self):
        run=json.loads((self.root/'run.json').read_text());run['status']='running'
        (self.root/'run.json').write_text(json.dumps(run))
        original=(self.root/'run.json').read_bytes()
        (self.root/'termination.json').write_text(json.dumps({'status':'failed','reason':'SIGSEGV','final_evaluation_available':False}))
        data=build_replay(self.root)
        self.assertEqual(data['status'],'failed')
        self.assertIsNone(data['evaluation_offline_only']['task_success'])
        self.assertEqual((self.root/'run.json').read_bytes(),original)
        run.update(status='passed',task_success=True)
        (self.root/'run.json').write_text(json.dumps(run))
        self.assertTrue(build_replay(self.root)['evaluation_offline_only']['task_success'])

    def test_replay_json_omits_goal_arrays_without_changing_raw_evidence_or_scores(self):
        metrics = {'task_success':False, 'official_task_success':False,
                   'goal_satisfaction_fraction':0.75,
                   'official_metrics':{'q_score':{'final':0.75}, 'time':{'simulator_steps':120}}}
        run_path = self.root/'run.json'
        run = json.loads(run_path.read_text())
        run.update(task_success=False, evaluation={**metrics,
                   'goal_options':[[True, False], [False, True]],
                   'initial_goal_options':[[False, False], [False, False]]})
        run_path.write_text(json.dumps(run))
        raw_paths = [run_path, self.root/'events.jsonl']
        hashes = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in raw_paths}
        result = render_replay(self.root)
        exported = json.loads((self.root/'replay.json').read_text())
        for data in (result, exported):
            self.assertFalse(data['evaluation_offline_only']['task_success'])
            self.assertEqual(data['evaluation_offline_only']['evaluation'], metrics)
        self.assertEqual({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in raw_paths}, hashes)
        self.assertEqual(json.loads(run_path.read_text())['evaluation'], run['evaluation'])

    def test_failed_plan_is_visible_but_does_not_overwrite_accepted_plan(self):
        self.events[4]['name']='update_plan'
        self.events[4]['arguments']={'reason':'proposed claim','subgoals':[{'id':'bad','status':'done'}]}
        self.save();step=build_replay(self.root)['steps'][1]
        self.assertEqual(step['plan'],[])
        self.assertFalse(step['decisions'][0]['accepted'])

    def test_script_injection_is_escaped_and_hidden_reasoning_not_loaded(self):
        render_replay(self.root)
        page=(self.root/'replay.html').read_text()
        self.assertNotIn('</script><script>alert(1)</script>',page)
        self.assertIn('\\u003c/script>',page)

    def test_exact_transport_passes(self):
        result=audit_rgb_transport(self.root,self.events,self.models)
        self.assertTrue(all(result['checks'].values()),result)
        self.assertEqual(result['image_content_count'],2)
        self.assertEqual(result['unique_model_image_count'],1)

    def append_observation_after_action(self):
        call=copy.deepcopy(self.events[2]);call.update(id='call3',request_id='3')
        result=copy.deepcopy(self.events[3]);result.update(id='res3',request_id='3')
        self.events.extend([call,result])
        model=copy.deepcopy(self.models[0])
        model['item']['result']['content'][0]['text']=json.dumps({**result['result'],'evidence_id':'res3'})
        self.models.append(model)

    def test_missing_response_is_not_image_corruption_and_later_delivery_is_checked(self):
        self.append_observation_after_action()
        for response,error,code in [
            (None,{'message':'timed out awaiting tools/call after 300s'},'tool_response_timeout'),
            ({'content':[]},None,'missing_tool_response'),
        ]:
            with self.subTest(code=code):
                self.models[1]['item'].update(id='timed_out_call',result=response,error=error,status='failed')
                result=audit_rgb_transport(self.root,self.events,self.models)
                self.assertFalse(result['checks']['model_results_match_simulator'])
                self.assertTrue(result['checks']['image_bytes_hashes_and_archive_match'])
                self.assertTrue(result['checks']['pixel_actions_use_latest_images'])
                self.assertEqual(result['image_content_count'],2)
                self.assertEqual(result['errors'],[{'event_id':'res2','model_item_id':'timed_out_call',
                    'tool':'act','error_type':'ToolResponseUnavailable','code':code,'response_received':False}])

    def test_timeout_does_not_hide_tampered_later_image_or_invalid_target(self):
        self.append_observation_after_action()
        self.models[1]['item'].update(result=None,error={'message':'tool timeout'},status='failed')
        self.models[1]['item']['arguments']['target']['image_ref']='never_received'
        self.models[2]['item']['result']['content'][1]['data']=base64.b64encode(b'tampered').decode()
        result=audit_rgb_transport(self.root,self.events,self.models)
        self.assertFalse(result['checks']['model_results_match_simulator'])
        self.assertFalse(result['checks']['image_bytes_hashes_and_archive_match'])
        self.assertFalse(result['checks']['pixel_actions_use_latest_images'])
        self.assertEqual(result['image_content_count'],2)

    def test_timeout_with_normal_finish_still_fails_complete_episode_evidence(self):
        self.append_observation_after_action()
        source={'commit':'abc','source_sha256':'digest','dirty':False}
        run={'run_id':'test','config':{'backend':'omnigibson','observation_mode':'rgb_only','agent_profile':'minimal'},
             'source':source,'task_success':False,'evaluation':{'official_task_success':False}}
        (self.root/'run.json').write_text(json.dumps(run))
        controller=self.root/'controller';controller.mkdir()
        (controller/'controller.json').write_text(json.dumps({'model':'fixture','agent_profile':'minimal',
            'status':'passed','exit_code':0,'source':source}))
        args={'outcome':'blocked','reason':'Observed limitation'}
        finish={'ok':True,'closed':True}
        self.events.extend([{'kind':'tool_call','id':'call4','name':'finish','arguments':args},
                            {'kind':'tool_result','id':'res4','name':'finish','result':finish}])
        self.models.append({'type':'item.completed','item':{'type':'mcp_tool_call','tool':'finish','arguments':args,
            'result':{'content':[{'type':'text','text':json.dumps({**finish,'evidence_id':'res4'})}]}}})
        for event in self.models:event['item']['server']='manipulation'
        self.save()
        stream=controller/'model_events.jsonl'
        stream.write_text(''.join(json.dumps(e)+'\n' for e in self.models))
        self.assertEqual(audit_episode(self.root,controller)['evidence_alignment'],'passed')
        self.models[1]['item'].update(result=None,error={'message':'timed out awaiting tools/call after 300s'},status='failed')
        stream.write_text(''.join(json.dumps(e)+'\n' for e in self.models))
        result=audit_episode(self.root,controller)
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['evidence_alignment'],'failed')
        self.assertTrue(result['checks']['formal_finish_in_model_trace'])
        self.assertTrue(result['checks']['controller_calls_match_simulator_exactly'])
        self.assertFalse(result['checks']['model_results_match_simulator'])
        self.assertTrue(result['checks']['image_bytes_hashes_and_archive_match'])

    def test_verbatim_public_messages_and_exact_mcp_text_without_private_reasoning(self):
        controller=self.root/'controller';controller.mkdir()
        (controller/'controller.json').write_text(json.dumps({'model':'fixture'}))
        original='Original public message.\nNo translation.'
        stream=[{'type':'item.started','item':{'type':'agent_message','id':'public','text':''}},
                {'type':'item.completed','item':{'type':'reasoning','text':'Returned public summary','encrypted_content':'HIDDEN_PRIVATE_REASONING'}},
                {'type':'item.completed','item':{'type':'agent_message','id':'public','text':original}},
                *self.models]
        (controller/'model_events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in stream))
        with patch('manipulation_agent.replay.audit_episode',return_value=None):
            result=render_replay(self.root,controller)
        self.assertEqual(result['steps'][0]['model_messages'][0]['text'],original)
        self.assertEqual(result['steps'][0]['model_result_text'],self.models[0]['item']['result']['content'][0]['text'])
        public=(self.root/'model_public_events.jsonl').read_text()
        self.assertIn('item.started',public)
        self.assertIn('Returned public summary',public)
        self.assertIn('Returned public summary',(self.root/'replay.html').read_text())
        self.assertNotIn('HIDDEN_PRIVATE_REASONING',public)
        self.assertNotIn('HIDDEN_PRIVATE_REASONING',(self.root/'replay.html').read_text())

    def test_tampered_image_fails(self):
        self.models[0]['item']['result']['content'][1]['data']=base64.b64encode(b'tampered').decode()
        self.assertFalse(audit_rgb_transport(self.root,self.events,self.models)['checks']['image_bytes_hashes_and_archive_match'])

    def test_provider_summary_aligns_by_timestamp_and_stays_separate(self):
        controller=self.root/'controller';controller.mkdir()
        (controller/'controller.json').write_text(json.dumps({'model':'fixture'}))
        (controller/'model_events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in self.models))
        self.events[4]['at']='2026-09-30T00:00:02Z';self.events[5]['at']='2026-09-30T00:00:03Z';self.save()
        summary={'source':'provider_returned_reasoning_summary','verbatim':True,'translated':False,
                 'text':'Provider text, exactly.\n**Original formatting**','at':'2026-09-30T00:00:01Z'}
        final={**summary,'text':'Later summary','at':'2026-09-30T00:00:04Z'}
        (controller/'model_reasoning_summaries.jsonl').write_text(json.dumps(summary)+'\n'+json.dumps(final)+'\n')
        with patch('manipulation_agent.replay.audit_episode',return_value=None):
            data=build_replay(self.root,controller)
        self.assertEqual(data['steps'][0]['model_reasoning_summaries'],[])
        self.assertEqual(data['steps'][1]['model_reasoning_summaries'],[summary])
        self.assertEqual(data['steps'][1]['model_messages'],[])
        self.assertEqual(data['model_final_reasoning_summaries'],[final])

    def test_oracle_extra_field_and_wrong_target_fail(self):
        result=json.loads(self.models[0]['item']['result']['content'][0]['text'])
        result['observation']['objects']=[{'id':'GT'}]
        self.models[0]['item']['result']['content'][0]['text']=json.dumps(result)
        self.models[1]['item']['arguments']['target']={'image_ref':'old','point':[.4,.6]}
        checks=audit_rgb_transport(self.root,self.events,self.models)['checks']
        self.assertFalse(checks['rgb_observation_allowlist'])
        self.assertFalse(checks['pixel_actions_use_latest_images'])

    def test_path_escape_has_no_displayable_image(self):
        capture=json.loads((self.root/'captures.jsonl').read_text())
        capture['images'][0]['file']='../../outside.jpg'
        (self.root/'captures.jsonl').write_text(json.dumps(capture)+'\n')
        self.assertIsNone(build_replay(self.root)['steps'][0]['after']['images'][0]['file'])
