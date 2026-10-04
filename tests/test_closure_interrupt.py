"""Finish must interrupt a cooperative active action before owner-thread scoring."""
import concurrent.futures
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from manipulation_agent import bridge
from manipulation_agent.contracts import SkillError
from manipulation_agent.deadline import EpisodeDeadline
from manipulation_agent.observations.mock_rgb import MockRGBBackend
from manipulation_agent.records import Recorder
from manipulation_agent.vision_harness import VisionHarness


class ClosureInterruptTests(unittest.TestCase):
    def test_finish_interrupts_active_action_and_persists_actual_score(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'episode'
            ready=threading.Event();entered=threading.Event();state={};failures=[]

            class ReadyServer(bridge.ThreadingHTTPServer):
                def __init__(self,*args,**kwargs):
                    super().__init__(*args,**kwargs)
                    state['url']=f'http://127.0.0.1:{self.server_port}';ready.set()

            def owner():
                try:
                    recorder=Recorder(output,{'backend':'mock','observation_mode':'rgb_only'})
                    backend=MockRGBBackend(output)
                    backend.deadline=EpisodeDeadline(time.time()+60)
                    harness=VisionHarness(backend,recorder,profile='minimal')
                    state['harness']=harness;state['backend']=backend
                    def long_action(*args,**kwargs):
                        state['step_limit']=args[2]
                        backend.on=True;backend.steps+=1
                        entered.set()
                        while True:
                            backend.deadline.check(changed=True)
                            time.sleep(.005)
                    backend.execute_visual=long_action
                    original=backend.evaluate
                    def evaluate():
                        state['evaluator_thread']=threading.get_ident()
                        return original()
                    backend.evaluate=evaluate
                    state['owner_thread']=threading.get_ident()
                    bridge.serve(harness,0)
                except BaseException as exc:
                    failures.append(exc);ready.set()

            with patch.object(bridge,'ThreadingHTTPServer',ReadyServer),patch.object(bridge,'CLOSED_REPLAY_SECONDS',0):
                thread=threading.Thread(target=owner,daemon=True);thread.start()
                try:
                    self.assertTrue(ready.wait(5));self.assertFalse(failures,failures)
                    url=state['url']
                    obs=bridge.rpc(url,'observe',{},'observe',timeout=2)['observation']
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                        action=pool.submit(bridge.rpc,url,'act',{'primitive':'toggle_on',
                            'target':{'image_ref':obs['images'][0]['image_ref'],'point':[.5,.5]},
                            'revision':obs['revision']},'act',timeout=5)
                        self.assertTrue(entered.wait(2))
                        capture=state['backend'].capture
                        result=bridge.rpc(url,'finish',{'outcome':'aborted','reason':'controller ended'},
                                          'supervisor-finish',timeout=3)
                        self.assertTrue(result['closed'])
                        interrupted=action.result(timeout=2)
                    self.assertEqual(interrupted['error']['code'],'episode_cancelled')
                    self.assertTrue(interrupted['error']['world_may_have_changed'])
                    self.assertEqual(state['backend'].capture,capture)
                    self.assertEqual(state['step_limit'],20000)
                    self.assertEqual(state['evaluator_thread'],state['owner_thread'])
                    run=json.loads((output/'run.json').read_text())
                    self.assertTrue(run['task_success'])
                    self.assertEqual(run['scoring']['status'],'passed')
                    self.assertFalse(run['deadline_expired_at_finish'])
                    self.assertEqual(run['agent_outcome'],'aborted')
                finally:
                    if 'harness' in state:state['harness'].deadline.request_stop()
                    thread.join(timeout=5)
                self.assertFalse(thread.is_alive());self.assertFalse(failures,failures)

    def test_invalid_finish_cannot_cancel_and_valid_finish_sets_only_stop_intent(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'episode'
            backend=MockRGBBackend(output)
            harness=VisionHarness(backend,Recorder(output,{}),profile='minimal')
            harness.request_finish({'outcome':'invalid','reason':'bad'},'finish')
            self.assertFalse(harness.deadline.stop_requested)
            harness.request_finish({'outcome':'aborted','reason':'stop'},'finish')
            self.assertTrue(harness.deadline.stop_requested)
            self.assertFalse(harness.closed)
            with self.assertRaises(SkillError) as caught:harness.deadline.check()
            self.assertEqual(caught.exception.code,'episode_cancelled')


if __name__=='__main__':unittest.main()
