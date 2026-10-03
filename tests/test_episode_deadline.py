"""CPU regressions for closure integrity and a shared startup-inclusive deadline."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from manipulation_agent.deadline import EpisodeDeadline
from manipulation_agent import bridge
from manipulation_agent.observations.mock_rgb import MockRGBBackend
from manipulation_agent.records import Recorder
from manipulation_agent.vision_harness import VisionHarness


class EpisodeDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.output=Path(self.folder.name)/'episode'
        self.recorder=Recorder(self.output,{'backend':'mock','observation_mode':'rgb_only'})
        self.backend=MockRGBBackend(self.output)
        self.backend.deadline=EpisodeDeadline(200)
        self.clock=100.0
        self.clock_patch=patch('manipulation_agent.deadline.time.time',side_effect=lambda:self.clock)
        self.clock_patch.start();self.addCleanup(self.clock_patch.stop)
        self.h=VisionHarness(self.backend,self.recorder,profile='minimal')

    def act(self,request_id='act'):
        return self.h.call('act',{'primitive':'toggle_on','revision':self.h.revision,
                                 'target':{'image_ref':self.h.snapshot['images'][0]['image_ref'],
                                           'point':[.5,.5]}},request_id)

    def test_video_failure_does_not_prevent_or_replace_score_and_finish_event(self):
        self.act()
        self.backend.finalize_video=Mock(side_effect=RuntimeError('incomplete frames'))
        self.backend.evaluate=Mock(wraps=self.backend.evaluate)
        args={'outcome':'achieved','reason':'fixture'}
        reply=self.h.call('finish',args,'finish')
        self.assertTrue(reply['closed'])
        self.backend.finalize_video.assert_not_called()
        persisted=json.loads((self.output/'run.json').read_text())
        self.assertTrue(persisted['task_success'])
        self.assertEqual(persisted['scoring']['status'],'passed')
        events=[json.loads(line) for line in (self.output/'events.jsonl').read_text().splitlines()]
        self.assertEqual(events[-1]['kind'],'tool_result')
        self.h.finalize_recording()
        persisted=json.loads((self.output/'run.json').read_text())
        self.assertTrue(persisted['task_success'])
        self.assertEqual(persisted['video']['status'],'failed')
        self.assertEqual(persisted['status'],'passed')
        self.clock=201
        self.assertEqual(self.h.call('finish',args,'finish'),reply)
        self.backend.evaluate.assert_called_once()
        self.assertEqual(self.act('after-close')['error']['code'],'episode_closed')

    def test_expired_queued_action_never_reaches_executor(self):
        self.backend.execute_visual=Mock(wraps=self.backend.execute_visual)
        self.clock=200
        reply=self.act()
        self.assertEqual(reply['error']['code'],'episode_timeout')
        self.backend.execute_visual.assert_not_called()
        self.assertEqual(self.h.actions,0)
        # Closure can still independently score, but a score cannot erase timeout.
        self.backend.on=True
        reply=self.h.call('finish',{'outcome':'aborted','reason':'deadline'},'deadline')
        self.assertTrue(reply['closed'])
        self.assertTrue(self.recorder.run['task_success'])
        self.assertEqual(self.recorder.run['episode_outcome'],'timeout')
        self.assertEqual(self.recorder.run['termination_reason'],'episode_deadline_exceeded')
        self.assertEqual(self.recorder.run['status'],'failed')

    def test_expiry_inside_action_stops_refresh_and_invalidates_old_revision(self):
        def execute(*args,**kwargs):
            self.backend.steps+=1
            self.clock=201
            self.backend.deadline.check(changed=True)
        self.backend.execute_visual=execute
        before_capture=self.backend.capture
        reply=self.act()
        self.assertEqual(reply['error']['code'],'episode_timeout')
        self.assertTrue(reply['error']['world_may_have_changed'])
        self.assertEqual(self.backend.capture,before_capture)
        self.assertEqual(self.h.revision,1)

    def test_evaluator_failure_is_missing_score_not_false_or_fabricated_q(self):
        self.backend.evaluate=Mock(side_effect=ValueError('invalid evaluator state'))
        reply=self.h.call('finish',{'outcome':'blocked','reason':'fixture'},'finish')
        self.assertTrue(reply['closed'])
        run=json.loads((self.output/'run.json').read_text())
        self.assertIsNone(run['task_success'])
        self.assertEqual(run['evaluation'],{})
        self.assertEqual(run['scoring']['status'],'failed')
        self.assertNotIn('Q',run)

    def test_finish_before_deadline_latches_execution_even_if_scoring_runs_late(self):
        def evaluate():
            self.clock=201
            return {'task_success':True,'protocol':'fixture'}
        self.backend.evaluate=evaluate
        self.h.call('finish',{'outcome':'achieved','reason':'fixture'},'finish')
        self.assertEqual(self.recorder.run['status'],'passed')
        self.assertEqual(self.recorder.run['execution_finished_at_unix'],100)
        self.assertFalse(self.recorder.run['deadline_expired_at_finish'])
        self.assertTrue(self.recorder.run['evaluation_finished_after_deadline'])
        self.assertNotIn('timeout',self.recorder.run)

    def test_bridge_automatically_closes_at_deadline_without_a_model_finish(self):
        self.clock=201
        with patch.object(bridge,'CLOSED_REPLAY_SECONDS',0):
            bridge.serve(self.h,0)
        self.assertTrue(self.h.closed)
        self.assertEqual(self.recorder.run['episode_outcome'],'timeout')
        events=[json.loads(line) for line in (self.output/'events.jsonl').read_text().splitlines()]
        self.assertTrue(any(e['kind']=='tool_result' and e['request_id']=='episode-deadline-finish' for e in events))


class ControllerDeadlineTests(unittest.TestCase):
    def test_handshake_consumes_remaining_task_time_before_model_wait(self):
        path=Path(__file__).resolve().parents[1]/'scripts'/'run_codex_controller.py'
        spec=importlib.util.spec_from_file_location('deadline_controller',path)
        controller=importlib.util.module_from_spec(spec);spec.loader.exec_module(controller)
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'controller';clock=[100.0];observed={}
            def preflight(*args,**kwargs):
                observed['preflight_timeout']=kwargs['timeout']
                clock[0]=130.0
                return {'status':'passed'}
            class Process:
                pid=123456;returncode=0
                def __init__(self,*args,**kwargs):self.stdout=kwargs['stdout']
                def communicate(self,text,timeout):
                    observed['model_timeout']=timeout
                    self.stdout.write(json.dumps({'type':'item.completed','item':{
                        'type':'mcp_tool_call','server':'manipulation','tool':'finish',
                        'result':{'content':[{'type':'text','text':'{"closed":true}'}]}}})+'\n')
                    clock[0]=149.0
                    return None,None
            decode=json.loads
            def decode_after_policy(text,*args,**kwargs):
                if text.startswith('{"type": "item.completed"'):clock[0]=151.0
                return decode(text,*args,**kwargs)
            argv=['controller','--model','fixture','--instruction','fixture','--mcp-command','fixture',
                  '--mcp-args-json','[]','--output',str(output),'--timeout','1800','--deadline-unix','150']
            with patch('sys.argv',argv), patch('manipulation_agent.deadline.time.time',side_effect=lambda:clock[0]), \
                 patch.object(controller,'check_server',side_effect=preflight), \
                 patch.object(controller.subprocess,'Popen',Process), \
                 patch.object(controller.subprocess,'check_output',return_value='fixture'), \
                 patch.object(controller,'source_version',return_value={'fixture':True}), \
                 patch.object(controller.json,'loads',side_effect=decode_after_policy), \
                 patch.object(controller,'export_summaries',return_value={}):
                self.assertEqual(controller.main(),0)
            self.assertEqual(observed,{'preflight_timeout':50.0,'model_timeout':20.0})
            record=json.loads((output/'controller.json').read_text())
            self.assertEqual(record['episode_deadline_unix'],150)
            self.assertEqual(record['effective_timeout_seconds'],20)
            self.assertEqual(record['policy_finished_at_unix'],149)
            self.assertNotIn('timeout',record)

    def test_timeout_with_partial_raw_json_still_persists_controller_terminal_record(self):
        path=Path(__file__).resolve().parents[1]/'scripts'/'run_codex_controller.py'
        spec=importlib.util.spec_from_file_location('timeout_controller',path)
        controller=importlib.util.module_from_spec(spec);spec.loader.exec_module(controller)
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'controller';clock=[100.0]
            class Process:
                pid=123456;returncode=-15
                def __init__(self,*args,**kwargs):self.stdout=kwargs['stdout']
                def communicate(self,text,timeout):
                    self.stdout.write('{"type":"unfinished')
                    clock[0]=151
                    raise controller.subprocess.TimeoutExpired('fixture',timeout)
                def wait(self,timeout):return self.returncode
            argv=['controller','--model','fixture','--instruction','fixture','--mcp-command','fixture',
                  '--mcp-args-json','[]','--output',str(output),'--timeout','1800','--deadline-unix','150']
            with patch('sys.argv',argv), patch('manipulation_agent.deadline.time.time',side_effect=lambda:clock[0]), \
                 patch.object(controller,'check_server',return_value={'status':'passed'}), \
                 patch.object(controller.subprocess,'Popen',Process), \
                 patch.object(controller.subprocess,'check_output',return_value='fixture'), \
                 patch.object(controller,'source_version',return_value={'fixture':True}), \
                 patch.object(controller.os,'killpg') as kill, \
                 patch.object(controller,'export_summaries',return_value={}):
                self.assertEqual(controller.main(),-15)
            kill.assert_called_once_with(123456,controller.signal.SIGTERM)
            record=json.loads((output/'controller.json').read_text())
            self.assertEqual(record['termination_reason'],'episode_deadline_exceeded')
            self.assertTrue(record['timeout'])
            self.assertEqual(record['event_decode_errors']['line_numbers'],[1])
            self.assertEqual((output/'model_events.jsonl').read_text(),'{"type":"unfinished')


if __name__=='__main__':unittest.main()
