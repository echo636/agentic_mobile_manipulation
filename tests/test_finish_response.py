"""Regression for finish replies blocked by synchronous replay publication."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from manipulation_agent import bridge
from manipulation_agent.observations.mock_rgb import MockRGBBackend
from manipulation_agent.records import Recorder
from manipulation_agent.vision_harness import VisionHarness


class FinishResponseTests(unittest.TestCase):
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
                    observation=bridge.rpc(url,'observe',{},'observe',timeout=2)['observation']
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
