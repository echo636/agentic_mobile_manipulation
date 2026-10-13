"""Shutdown protocol regressions; mock pixels are not robot task evidence."""
import concurrent.futures
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import urllib.request

from manipulation_agent import bridge
from manipulation_agent.closure import closure_watchdog
from manipulation_agent.contracts import Budget
from manipulation_agent.observations.mock_rgb import MockRGBBackend
from manipulation_agent.records import Recorder
from manipulation_agent.vision_harness import VisionHarness
import test_batch_lifecycle as lifecycle


class ClosureProtocolTests(unittest.TestCase):
    def test_slow_scoring_and_persistence_are_not_reply_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'episode';ready=threading.Event()
            evaluating=threading.Event();allow_score=threading.Event()
            persisting=threading.Event();allow_persist=threading.Event()
            state={};failures=[]
            # Source provenance/snapshot copying may be slow on shared storage;
            # it is initialization, outside the closure timing being tested.
            rec=Recorder(output,{'backend':'mock'})
            class Server(bridge.ThreadingHTTPServer):
                def __init__(self,*args,**kwargs):
                    super().__init__(*args,**kwargs)
                    state['url']=f'http://127.0.0.1:{self.server_port}';ready.set()
            def owner():
                try:
                    backend=MockRGBBackend(output);original=backend.evaluate
                    def evaluate():
                        evaluating.set()
                        if not allow_score.wait(5):raise RuntimeError('test scoring not released')
                        return original()
                    backend.evaluate=evaluate;finish=rec.finish
                    def persist(*args,**kwargs):
                        persisting.set()
                        if not allow_persist.wait(5):raise RuntimeError('test persistence not released')
                        return finish(*args,**kwargs)
                    rec.finish=persist
                    h=VisionHarness(backend,rec,Budget(wall_seconds=None),agent_step_limit=50)
                    state['h']=h;bridge.serve(h,0)
                except BaseException as exc:failures.append(exc);ready.set()
            def health():
                opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(state['url']+'/healthz',timeout=2) as response:return json.load(response)
            with patch.object(bridge,'ThreadingHTTPServer',Server),patch.object(bridge,'CLOSED_REPLAY_SECONDS',.5):
                thread=threading.Thread(target=owner,daemon=True);thread.start()
                try:
                    self.assertTrue(ready.wait(30));self.assertFalse(failures)
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                        pending=pool.submit(bridge.rpc,state['url'],'finish',
                            {'outcome':'blocked','reason':'fixture'},'finish',timeout=10)
                        self.assertTrue(evaluating.wait(3))
                        v=health();self.assertTrue(v['closed']);self.assertEqual(v['closure']['phase'],'scoring')
                        self.assertNotIn('task_success',v['closure'])
                        self.assertFalse(pending.done())
                        allow_score.set();self.assertTrue(persisting.wait(3))
                        self.assertEqual(health()['closure']['phase'],'persisting')
                        allow_persist.set();self.assertTrue(pending.result(3)['closed'])
                        self.assertEqual(health()['closure']['phase'],'result_ready')
                finally:
                    allow_score.set();allow_persist.set();thread.join(30)
            self.assertFalse(thread.is_alive());self.assertFalse(failures,failures)
            closure=json.loads((output/'closure.json').read_text())
            self.assertIn('reply_sent_at_unix',closure)
            self.assertEqual(closure['origin'],'controller')
            self.assertIs(json.loads((output/'run.json').read_text())['task_success'],False)

    def test_phase_watchdogs_are_bounded_and_repeated_polls_do_not_extend_them(self):
        row={'episode_closure':{'phase':'scoring'}}
        self.assertIsNone(closure_watchdog(row,now=0))
        for t in [21,120,599]:self.assertIsNone(closure_watchdog(row,now=t))
        self.assertEqual(closure_watchdog(row,now=600),'scoring_timeout')
        row['episode_closure']={'phase':'persisting'}
        self.assertIsNone(closure_watchdog(row,now=601))
        row['episode_closure']={'phase':'result_ready'}
        self.assertIsNone(closure_watchdog(row,now=610))
        self.assertIsNone(closure_watchdog(row,now=631))
        # The saved monitor, rather than a new process-local timer, survives adoption.
        adopted=json.loads(json.dumps(row))
        self.assertEqual(closure_watchdog(adopted,now=730),'client_exit_timeout')

    def test_scoring_exception_is_not_a_false_score_or_unbounded_closure(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'episode';rec=Recorder(out,{})
            backend=MockRGBBackend(out);backend.evaluate=Mock(side_effect=RuntimeError('fixture'))
            h=VisionHarness(backend,rec)
            reply=h.call('finish',{'outcome':'blocked','reason':'fixture'},'f')
            self.assertTrue(reply['closed']);self.assertIsNone(rec.run['task_success'])
            self.assertEqual(rec.run['scoring']['status'],'failed')
            self.assertEqual(h.closure.snapshot()['phase'],'result_ready')
            self.assertEqual(h.call('finish',{'outcome':'blocked','reason':'fixture'},'f'),reply)
            backend.evaluate.assert_called_once()


class ClosureSupervisorTests(unittest.TestCase):
    def runner(self,tmp):
        b=lifecycle.LifecycleTests().runner(tmp)
        b.manifest={'episode_budget_policy':'agent_steps','controller_client':'codex_cli'}
        b.refresh_startup_progress=Mock()
        b.controller_metadata=Mock(return_value={})
        return b

    def test_healthy_closure_can_take_more_than_twenty_seconds_per_phase(self):
        with tempfile.TemporaryDirectory() as tmp:
            b=self.runner(tmp);row=b.rows[0]
            row.update(execution_started_at_unix=1,startup_deadline_unix=0,episode_deadline_unix=10)
            phases=iter(['scoring','persisting','result_ready'])
            def closed(_):row['episode_closure']={'phase':next(phases)};return True
            b.step_episode_closed=closed;b.stop_controller=Mock()
            process=Mock();process.poll.side_effect=[None,None,None,0];process.last_heartbeat=0
            clock=[100]
            with patch.object(lifecycle.batch.time,'time',side_effect=lambda:clock[0]),patch.object(
                    lifecycle.batch.time,'sleep',side_effect=lambda _:clock.__setitem__(0,clock[0]+31)):
                b.wait_controller(row,process,Path(tmp))
            b.stop_controller.assert_not_called()
            self.assertNotIn('episode_timed_out',row)

    def test_stuck_owned_client_is_cleaned_without_overwriting_task_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            b=self.runner(tmp);out=Path(tmp)/'controller';out.mkdir()
            b._config['controller_exit_grace_seconds']=0
            row=b.rows[0];row.update(execution_started_at_unix=1,startup_deadline_unix=0,
                task_success=False,execution_finished_at='now',deadline_expired_at_finish=False,
                execution_finished_at_unix=1,episode_closure={'phase':'result_ready','origin':'controller'})
            b.step_episode_closed=Mock(return_value=True)
            child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)',str(out)],start_new_session=True)
            (out/'controller.json').write_text(json.dumps({'pid':child.pid,'status':'running'}))
            item={'type':'mcp_tool_call','server':'manipulation','tool':'finish','arguments':{},
                  'result':{'content':[{'type':'text','text':'{"ok":true,"closed":true}'}]}}
            (out/'model_events.jsonl').write_text(json.dumps({'type':'item.completed','item':item})+'\n')
            try:
                b.wait_controller(row,child,out)
                self.assertIsNotNone(child.poll())
                meta=json.loads((out/'controller.json').read_text())
                b.record_controller_end(row,out,meta);b.complete_row(row)
                self.assertEqual(row['controller_status'],'terminated')
                self.assertTrue(row['controller_finish_acknowledged'])
                self.assertEqual(row['termination_reason'],'task_goal_not_satisfied')
                self.assertEqual(row['controller_cleanup']['reason'],'client_exit_timeout')
                self.assertEqual(meta['status'],'running')  # Raw client evidence is unchanged.
            finally:
                if child.poll() is None:child.kill();child.wait(5)

    def test_service_failure_stays_controller_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            b=self.runner(tmp);row=b.rows[0]
            row.update(execution_finished_at='now',task_success=False,controller_status='failed',
                       episode_closure={'phase':'result_ready','origin':'supervisor'})
            b.complete_row(row)
            self.assertEqual(row['termination_reason'],'controller_failed')

    def test_existing_finish_is_not_replaced_by_supervisor_finish(self):
        with tempfile.TemporaryDirectory() as tmp:
            b=self.runner(tmp);row=b.rows[0];row.update(port=1,simulator_unit='owned')
            b.unit_state=Mock(side_effect=[{'ActiveState':'active'}, {'ActiveState':'inactive','MainPID':'0'}])
            b.health=Mock(return_value={'closed':True,'closure':{'phase':'scoring'}})
            b.ssh=Mock()
            b.finish_and_wait(row,{})
            b.ssh.assert_not_called()

    def test_health_outage_preserves_accepted_finish(self):
        with tempfile.TemporaryDirectory() as tmp:
            b=self.runner(tmp);row=b.rows[0]
            row.update(port=1,simulator_unit='owned',episode_closure={'phase':'scoring'})
            b.unit_state=Mock(return_value={'ActiveState':'active','MainPID':'1'})
            b.health=Mock(return_value=None);b.ssh=Mock()
            self.assertTrue(b.step_episode_closed(row))
            b.unit_state.side_effect=[{'ActiveState':'active'}, {'ActiveState':'inactive','MainPID':0}]
            b.finish_and_wait(row,{})
            b.ssh.assert_not_called()

    def test_finish_wait_allows_each_phase_its_own_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            b=self.runner(tmp);row=b.rows[0];row.update(port=1,simulator_unit='owned')
            b._config['finish_stage_timeout_seconds']=10
            b.unit_state=Mock(return_value={'ActiveState':'active','MainPID':'1'})
            b.health=Mock(return_value={'closed':True});b.ssh=Mock()
            clock=[100]
            def closed(_):
                row['episode_closure']={'phase':'scoring' if clock[0]<110 else 'persisting'}
                return True
            b.step_episode_closed=closed
            with patch.object(lifecycle.batch.time,'time',side_effect=lambda:clock[0]),patch.object(
                    lifecycle.batch.time,'sleep',side_effect=lambda _:clock.__setitem__(0,clock[0]+2)):
                b.finish_and_wait(row,{})
            self.assertEqual(clock[0],120)
            self.assertEqual(row['simulator_closure_timeout'],'persisting_timeout')
            b.ssh.assert_not_called()

    def test_adoption_does_not_restart_client_exit_grace(self):
        with tempfile.TemporaryDirectory() as tmp:
            b=self.runner(tmp);row=b.rows[0];out=Path(tmp)/'controller';out.mkdir()
            row.update(controller_output=str(out),simulator_output=str(Path(tmp)/'sim'),
                simulator_unit='owned',controller_wrapper_pid=123,execution_started_at_unix=1,
                startup_deadline_unix=0,episode_closure={'phase':'result_ready'},
                closure_watchdog={'phase':'result_ready','started_at_unix':1})
            b._worker_local.config=b.workers[0];b._worker_local.defer_archive=True
            b.unit_state=Mock(return_value={'ActiveState':'inactive','MainPID':'0'})
            b.step_episode_closed=Mock(return_value=True);b.stop_controller=Mock()
            b.finish_and_wait=Mock();b.cleanup_owned_unit=Mock()
            with patch.object(lifecycle.batch,'controller_process_alive',return_value=True),patch.object(
                    lifecycle.batch.time,'time',return_value=122):
                b.adopt_one(row)
            b.stop_controller.assert_called_once_with(None,out,wrapper_pid=123)
            self.assertEqual(row['closure_watchdog']['started_at_unix'],1)
            self.assertEqual(row['controller_cleanup']['reason'],'client_exit_timeout')


if __name__=='__main__':unittest.main()
