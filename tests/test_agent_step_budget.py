"""Step-budget regressions: these CPU fixtures are not robot task evidence."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from manipulation_agent import bridge
from manipulation_agent.contracts import Budget
from manipulation_agent.deadline import EpisodeDeadline, write_execution_clock, validate_execution_clock, tool_wait_seconds
from manipulation_agent.observations.mock_rgb import MockRGBBackend
from manipulation_agent.records import Recorder, read_run
from manipulation_agent.vision_harness import VisionHarness
from manipulation_agent.clients.base import run_process
from manipulation_agent.clients.types import PreparedProject
import test_batch_lifecycle as lifecycle
batch=lifecycle.batch


class AgentStepTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.root=Path(folder.name);self.output=self.root/'episode'
        self.recorder=Recorder(self.output,{'backend':'mock'})
        self.backend=MockRGBBackend(self.output)
        self.backend.deadline=EpisodeDeadline()
        self.h=VisionHarness(self.backend,self.recorder,Budget(wall_seconds=None),agent_step_limit=2)
        self.h.start_standalone_clock()

    def act(self, rid='action', **overrides):
        args={'primitive':'toggle_on','revision':self.h.revision,
              'target':{'image_ref':self.h.snapshot['images'][0]['image_ref'],'point':[.5,.5]}}
        args.update(overrides)
        return self.h.call('act',args,rid)

    def test_clock_is_immutable_and_never_expires(self):
        path=self.root/'clock.json'
        clock=write_execution_clock(path,None,max_agent_steps=50)
        with patch('manipulation_agent.deadline.time.time',return_value=clock['execution_started_at_unix']+86400):
            deadline=EpisodeDeadline(clock_path=path)
            self.assertFalse(deadline.expired);self.assertIsNone(deadline.remaining())
            self.assertEqual(write_execution_clock(path,None,max_agent_steps=50),clock)
        self.assertIsNone(clock['episode_deadline_unix'])
        for invalid in ({'max_agent_steps':True},{'max_agent_steps':0},{'episode_deadline_unix':3000},
                        {'execution_budget_seconds':1800}):
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):
                validate_execution_clock({**clock,**invalid})

    def test_failed_attempts_count_and_cached_retries_are_free(self):
        args={'primitive':'toggle_on','revision':999,'target':None}
        first=self.h.call('act',args,'stale')
        self.assertFalse(first['ok']);self.assertEqual(self.h.agent_steps,1)
        self.assertEqual(self.h.call('act',args,'stale'),first)
        self.assertEqual(self.h.agent_steps,1)
        invalid=self.h.call('look',{'unexpected':'schema error'},'bad-schema')
        self.assertFalse(invalid['ok']);self.assertEqual(self.h.agent_steps,2)
        self.assertEqual(self.act('third')['error']['code'],'agent_step_budget_exhausted')
        self.assertEqual(self.backend.steps,0)
        self.assertTrue(self.h.call('finish',{'outcome':'blocked','reason':'fixture'},'end')['closed'])
        run=read_run(self.output)
        self.assertEqual(run['termination_reason'],'agent_step_budget_exhausted')
        self.assertFalse(run['task_success']);self.assertEqual(run['agent_steps'],2)
        self.assertNotIn('timeout',run)

    def test_execution_error_counts_and_read_only_tools_are_free(self):
        for i in range(4):self.h.call('initialize',{},str(i))
        self.h.call('list_skills',{},'skills')
        self.assertEqual(self.h.agent_steps,0)
        self.backend.fail_next=True
        self.assertFalse(self.act('fails')['ok'])
        self.assertEqual(self.h.agent_steps,1)
        self.assertEqual(self.h.actions,1)
        self.assertTrue(self.h.call('look',{'revision':self.h.revision,'yaw_degrees':30},'look')['ok'])
        self.assertEqual(self.h.agent_steps,2)

    def test_final_action_survives_old_limits_and_late_time_then_scores_success(self):
        self.act('one')
        self.h.actions=80;self.h.calls=240;self.backend.steps=20000
        original=self.backend.execute_visual
        before=self.backend.capture
        def slow(*args,**kwargs):
            self.assertGreater(args[2],700)
            self.assertIsNone(self.h.agent_steps_exhausted_at)
            self.assertFalse(self.h.agent_step_closure_due())
            return original(*args,**kwargs)
        self.backend.execute_visual=slow
        with patch('manipulation_agent.deadline.time.time',return_value=time.time()+86400):
            reply=self.act('last')
        self.assertTrue(reply['ok']);self.assertGreater(self.backend.capture,before)
        self.assertEqual(reply['execution_budget']['steps_remaining'],0)
        self.assertFalse(self.h.agent_step_closure_due())
        self.assertTrue(self.h.call('finish',{'outcome':'achieved','reason':'green'},'end')['closed'])
        run=read_run(self.output)
        self.assertTrue(run['task_success']);self.assertEqual(run['status'],'passed')
        self.assertEqual(run['agent_steps'],2);self.assertFalse(run['deadline_expired_at_finish'])

    def test_fifty_is_exact_limit(self):
        self.h.agent_step_limit=50
        for i in range(50):self.assertTrue(self.act(str(i))['ok'])
        steps=self.backend.steps
        self.assertEqual(self.act('51')['error']['code'],'agent_step_budget_exhausted')
        self.assertEqual(self.backend.steps,steps);self.assertEqual(self.h.agent_steps,50)

    def test_closure_after_budget_still_evaluates_without_model_finish(self):
        self.h.call('act',{},'bad1');self.h.call('act',{},'bad2')
        self.h.agent_steps_exhausted_at=time.monotonic()-121
        with patch.object(bridge,'CLOSED_REPLAY_SECONDS',0):bridge.serve(self.h,0)
        self.assertTrue(self.h.closed)
        self.assertEqual(read_run(self.output)['termination_reason'],'agent_step_budget_exhausted')

    def test_no_timeout_process_and_transport(self):
        project=PreparedProject([sys.executable,'-c','print("probe")'],self.root,'')
        result=run_process(project,self.root,None)
        self.assertEqual(result.returncode,0);self.assertFalse(result.timed_out)
        self.assertGreater(tool_wait_seconds(None),86400)


class StepSupervisorTests(unittest.TestCase):
    def runner(self, root):
        b=lifecycle.LifecycleTests().runner(root)
        b.manifest={'episode_budget_policy':'agent_steps','max_agent_steps':50}
        b.source=Path(root);b._config.update(id='a',gpu=0,ssh=['ssh','host'],sim_python='python')
        return b

    def test_armed_clock_ignores_expired_startup_watchdog(self):
        with tempfile.TemporaryDirectory() as tmp:
            b=self.runner(tmp);controller=Path(tmp)/'controller';controller.mkdir()
            clock=write_execution_clock(Path(tmp)/'clock.json',None,max_agent_steps=50)
            (controller/'controller.json').write_text(json.dumps({'execution_clock':clock,**clock}))
            row=b.rows[0];row['startup_deadline_unix']=time.time()-3600
            b.refresh_startup_progress=Mock();b.step_episode_closed=Mock(return_value=False)
            b.stop_controller=Mock()
            process=Mock();process.poll.side_effect=[None,0];process.last_heartbeat=time.monotonic()
            with patch.object(batch.time,'sleep'):b.wait_controller(row,process,controller)
            b.stop_controller.assert_not_called()
            self.assertIsNone(row['episode_deadline_unix']);self.assertEqual(row['budget_basis'],'agent_steps')
            self.assertNotIn('startup_timed_out',row)
            # The same original clock is recovered from persisted metadata on adoption.
            adopted={'run_id':'0','startup_deadline_unix':0}
            b.controller_metadata(adopted,controller)
            self.assertEqual(adopted['execution_started_at_unix'],clock['execution_started_at_unix'])
            self.assertIsNone(adopted['episode_deadline_unix'])

    def test_step_clock_command_discards_legacy_duration(self):
        with tempfile.TemporaryDirectory() as tmp:
            b=self.runner(tmp)
            command=b.execution_clock_command(Path(tmp)/'clock',1800)
            self.assertIn('None,max_agent_steps=50',command[-1])
            self.assertNotIn('1800',command[-1])

    def test_unavailable_remote_health_is_not_proof_of_termination(self):
        with tempfile.TemporaryDirectory() as tmp:
            b=self.runner(tmp);row={'run_id':'fixture','port':1,'simulator_unit':'owned'}
            b.health=Mock(return_value=None)
            b.unit_state=Mock(return_value={'query_status':'unavailable'})
            self.assertFalse(b.step_episode_closed(row))
            b._worker_local.closure_poll=None
            b.unit_state.return_value={'ActiveState':'inactive','MainPID':'0'}
            self.assertTrue(b.step_episode_closed(row))

    def test_adoption_keeps_running_after_original_startup_deadline(self):
        with tempfile.TemporaryDirectory() as tmp:
            b=self.runner(tmp);controller=Path(tmp)/'controller';controller.mkdir()
            clock=write_execution_clock(Path(tmp)/'clock.json',None,max_agent_steps=50)
            (controller/'controller.json').write_text(json.dumps({'execution_clock':clock,**clock}))
            row=b.rows[0];row.update(status='running',controller_output=str(controller),
                simulator_output=tmp,simulator_unit='owned',controller_wrapper_pid=123,
                startup_deadline_unix=time.time()-3600)
            b.unit_state=Mock(return_value={'ActiveState':'active','Description':'BEHAVIOR100 owned 0'})
            b.refresh_startup_progress=Mock();b.step_episode_closed=Mock(return_value=False)
            b.stop_controller=Mock();b.finish_and_wait=Mock();b._worker_local.defer_archive=True
            b.cleanup_owned_unit=lambda row:row.update(simulator_cleanup={'status':'passed'})
            with patch.object(batch,'controller_process_alive',side_effect=[True,False]),patch.object(batch.time,'sleep'):
                b.adopt_one(row)
            b.stop_controller.assert_not_called()
            self.assertNotIn('startup_timed_out',row);self.assertNotIn('failure',row)
            self.assertIsNone(row['episode_deadline_unix'])
