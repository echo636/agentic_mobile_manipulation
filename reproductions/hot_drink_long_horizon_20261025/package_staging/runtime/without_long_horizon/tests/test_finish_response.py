"""Regression for finish replies blocked by synchronous replay publication."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from manipulation_agent import bridge
from manipulation_agent.contracts import Budget, SkillError
from manipulation_agent.deadline import EpisodeDeadline
from manipulation_agent.observations.mock_rgb import MockRGBBackend
from manipulation_agent.records import Recorder
from manipulation_agent.vision_harness import VisionHarness


class FinishResponseTests(unittest.TestCase):
    def test_hot_drink_rejects_early_blocked_without_stopping_recovery(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'episode'
            backend=MockRGBBackend(output)
            backend.high_level_harness=True
            backend.deadline=EpisodeDeadline(time.time()+3600)
            harness=VisionHarness(backend,Recorder(output,{}),budget=Budget(max_actions=80),profile='minimal')
            arguments={'outcome':'blocked','reason':'Selected surface was invalid twice'}
            harness._last_action_error='invalid_visual_target'
            harness.actions=5
            harness.request_finish(arguments,'finish-early')
            self.assertFalse(harness.deadline.stop_requested)
            rejected=harness.call('finish',arguments,'finish-early')
            self.assertFalse(rejected['ok'])
            self.assertEqual(rejected['error']['code'],'premature_finish')
            self.assertFalse(harness.closed)
            observation=harness.snapshot
            target={'image_ref':observation['images'][0]['image_ref'],'point':[.5,.5]}
            continued=harness.call('act',{'primitive':'toggle_on','target':target,
                                          'revision':observation['revision']},'continue')
            self.assertTrue(continued['ok'])
            self.assertEqual(continued['actions_used'],6)

    def test_hot_drink_accepts_blocked_after_action_budget_or_fatal_error(self):
        for exhausted in (True,False):
            with self.subTest(exhausted=exhausted),tempfile.TemporaryDirectory() as folder:
                output=Path(folder)/'episode'
                backend=MockRGBBackend(output)
                backend.high_level_harness=True
                backend.deadline=EpisodeDeadline(time.time()+3600)
                harness=VisionHarness(backend,Recorder(output,{}),budget=Budget(max_actions=80),profile='minimal')
                if exhausted:
                    harness.actions=80
                else:
                    harness._last_action_error='navigation_invalid_start'
                arguments={'outcome':'blocked','reason':'Cannot proceed'}
                harness.request_finish(arguments,'finish')
                self.assertTrue(harness.deadline.stop_requested)
                result=harness.call('finish',arguments,'finish')
                self.assertTrue(result['ok'])
                self.assertTrue(result['closed'])

    def test_hot_drink_cannot_escape_early_blocked_gate_by_changing_outcome(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'episode'
            backend=MockRGBBackend(output)
            backend.high_level_harness=True
            backend.deadline=EpisodeDeadline(time.time()+3600)
            harness=VisionHarness(backend,Recorder(output,{}),profile='minimal')
            for outcome in ('aborted','achieved'):
                arguments={'outcome':outcome,'reason':'Cannot complete'}
                harness.request_finish(arguments,f'early-{outcome}')
                self.assertFalse(harness.deadline.stop_requested)
                result=harness.call('finish',arguments,f'early-{outcome}')
                self.assertFalse(result['ok'])
                self.assertEqual(result['error']['code'],
                                 'supervisor_only' if outcome=='aborted' else 'premature_finish')
                self.assertFalse(harness.closed)
            observation=harness.snapshot
            target={'image_ref':observation['images'][0]['image_ref'],'point':[.5,.5]}
            result=harness.call('act',{'primitive':'toggle_on','target':target,
                                       'revision':observation['revision']},'continue')
            self.assertTrue(result['ok'])
            achieved={'outcome':'achieved','reason':'Completed'}
            harness.request_finish(achieved,'achieved-after-work')
            self.assertFalse(harness.deadline.stop_requested)
            result=harness.call('finish',achieved,'achieved-after-work')
            self.assertTrue(result['ok'])
            self.assertTrue(result['closed'])

    def test_demo_rejects_false_time_exhaustion_without_cancelling_episode(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'episode'
            recorder=Recorder(output,{'backend':'mock','observation_mode':'rgb_only'})
            backend=MockRGBBackend(output)
            backend.demo_motion=True
            backend.deadline=EpisodeDeadline(time.time()+3600)
            harness=VisionHarness(backend,recorder,profile='minimal')
            arguments={'outcome':'blocked','reason':'Could not complete within the available execution window.'}
            first=harness.call('initialize',{},'initialize')
            self.assertGreater(first['time_remaining_seconds'],3500)

            harness.request_finish(arguments,'finish-early')
            self.assertFalse(harness.deadline.stop_requested)
            with self.assertRaises(SkillError) as rejected:
                harness.finish(**arguments)
            self.assertEqual(rejected.exception.code,'time_budget_available')
            self.assertFalse(harness.closed)

    def test_http_finish_persists_score_and_returns_before_offline_render(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'episode'
            ready=threading.Event();state={};failures=[]

            class ReadyServer(bridge.ThreadingHTTPServer):
                def __init__(self,*args,**kwargs):
                    super().__init__(*args,**kwargs)
                    state['url']=f'http://127.0.0.1:{self.server_port}'
                    ready.set()

            def simulator_owner():
                try:
                    recorder=Recorder(output,{'backend':'mock','observation_mode':'rgb_only'})
                    state['render']=recorder.render
                    recorder.render=Mock(side_effect=AssertionError('Replay rendering blocked the finish RPC'))
                    state['recorder']=recorder
                    backend=MockRGBBackend(output)
                    harness=VisionHarness(backend,recorder,profile='minimal')
                    bridge.serve(harness,0)
                except BaseException as exc:
                    failures.append(exc)
                    ready.set()

            with patch.object(bridge,'ThreadingHTTPServer',ReadyServer):
                thread=threading.Thread(target=simulator_owner,daemon=True)
                thread.start()
                try:
                    self.assertTrue(ready.wait(5))
                    self.assertFalse(failures,failures)
                    url=state['url']
                    observation=bridge.rpc(url,'initialize',{},'initialize',timeout=2)['observation']
                    bridge.rpc(url,'act',{'primitive':'toggle_on','target':{
                        'image_ref':observation['images'][0]['image_ref'],'point':[.5,.5]},
                        'revision':observation['revision']},'act',timeout=2)
                    arguments={'outcome':'achieved','reason':'Observed the changed RGB'}
                    result=bridge.rpc(url,'finish',arguments,'finish',timeout=2)
                    self.assertTrue(result['closed'])
                    self.assertTrue(result['ok'])
                    self.assertNotIn('evaluation',result)
                    self.assertEqual(bridge.rpc(url,'finish',arguments,'finish',timeout=2),result)
                    run=json.loads((output/'run.json').read_text())
                    self.assertIs(run['task_success'],True)
                    events=[json.loads(line) for line in (output/'events.jsonl').read_text().splitlines()]
                    self.assertEqual(sum(e['kind']=='independent_evaluation' for e in events),1)
                    self.assertEqual(events[-1]['kind'],'tool_result')
                    self.assertEqual(events[-1]['request_id'],'finish')
                    state['recorder'].render.assert_not_called()
                    self.assertFalse((output/'replay.html').exists())
                finally:
                    thread.join(timeout=13)  # Existing bridge close/retry window.
                self.assertFalse(thread.is_alive())
                self.assertFalse(failures,failures)
            raw={name:(output/name).read_bytes() for name in ('run.json','events.jsonl')}
            state['render']()  # The existing offline publication path remains usable.
            self.assertTrue((output/'replay.html').is_file())
            self.assertTrue((output/'replay.json').is_file())
            self.assertEqual(raw,{name:(output/name).read_bytes() for name in raw})


if __name__=='__main__':
    unittest.main()
