import importlib.util
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from manipulation_agent.batch_lifecycle import FileLease, WorkerLease, classify_episode_outcome, validate_manifest_coverage, preferred_gpu_worker

spec=importlib.util.spec_from_file_location('lifecycle_batch',Path(__file__).resolve().parents[1]/'scripts/run_behavior100.py')
batch=importlib.util.module_from_spec(spec);spec.loader.exec_module(batch)


class LifecycleTests(unittest.TestCase):
    def test_admission_prefers_real_057_headroom_without_claiming_busy_or_foreign_gpus(self):
        workers=[{'id':f's115-gpu{i}','gpu':i,'ssh':['ssh','s115'],'minimum_free_gpu_mib':18432}
                 for i in range(4)]
        workers.append({'id':'foreign','gpu':4,'ssh':['ssh','s134'],'minimum_free_gpu_mib':18432})
        snapshot='0, UUID0, 23525, 49140, 580.95.05\n2, UUID2, 14537, 49140, 580.95.05\n4, UUID4, 0, 49140, 580.95.05'
        self.assertEqual(preferred_gpu_worker(workers,{},'s115',snapshot),'s115-gpu2')
        for state in ({'stage':'running'}, {'stage':'disabled'}, {'checks':{'bridge_port_free':False}}):
            self.assertEqual(preferred_gpu_worker(workers,{'s115-gpu2':state},'s115',snapshot),'s115-gpu0')
        self.assertIsNone(preferred_gpu_worker(workers,{},'s115',''))

    def runner(self, root, count=2):
        b=batch.Batch.__new__(batch.Batch)
        b.root=Path(root);b._worker_local=threading.local();b.lock=threading.RLock()
        b._config={'shared_lease_dir':str(b.root/'leases'),'resource_poll_seconds':.01,
                   'worker_yield_seconds':0,'archive_workers':1,'max_pending_archives':2}
        b.workers=[{'id':'a','gpu':0,'ssh':['ssh','host'],'expected_gpu_uuid':'physical0'}]
        b.rows=[{'index':i,'run_id':str(i),'status':'planned'} for i in range(count)]
        b.queue=queue.Queue();b.worker_states={};b.publish=Mock();b.journal=Mock()
        b.update=lambda row,**kw:row.update(kw);b.resource_admission=lambda:True
        return b

    def test_real_process_gpu_and_host_leases_survive_independent_callers(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker={'id':'other-arm','gpu':0,'ssh':['ssh','host'],'expected_gpu_uuid':'physical0'}
            code='''import json,sys\nfrom manipulation_agent.batch_lifecycle import WorkerLease\nw=json.loads(sys.argv[2]); l=WorkerLease(sys.argv[1],w,host_budget_gib=28)\nassert l.acquire(); l.record(unit="owned.service",run_id="original",released=False)\nprint("locked",flush=True); sys.stdin.readline(); l.release(cleared=True)\n'''
            process=subprocess.Popen([sys.executable,'-c',code,tmp,json.dumps(worker)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
            try:
                self.assertEqual(process.stdout.readline().strip(),'locked')
                gpu=WorkerLease(tmp,worker,host_budget_gib=56);self.assertFalse(gpu.acquire())
                other=WorkerLease(tmp,{**worker,'gpu':1,'expected_gpu_uuid':'physical1'},host_budget_gib=28)
                self.assertFalse(other.acquire())
                process.stdin.write('\n');process.stdin.flush();process.wait(5)
                self.assertTrue(other.acquire());other.release(cleared=True)
            finally:
                if process.poll() is None:process.kill();process.wait()
                process.stdin.close();process.stdout.close()

    def test_record_error_still_unlocks_both_resources(self):
        with tempfile.TemporaryDirectory() as tmp:
            w={'id':'a','gpu':0,'ssh':['ssh','host']}
            lease=WorkerLease(tmp,w,host_budget_gib=28);self.assertTrue(lease.acquire())
            with patch.object(lease.gpu,'record',side_effect=OSError('disk')):
                with self.assertRaises(OSError):lease.release(cleared=True)
            fresh=WorkerLease(tmp,w,host_budget_gib=28);self.assertTrue(fresh.acquire());fresh.release()

    def test_resource_wait_does_not_claim_or_start_deadline(self):
        with tempfile.TemporaryDirectory() as tmp:
            b=self.runner(tmp);checks=0
            def admit():
                nonlocal checks
                checks+=1
                if checks<3:
                    self.assertTrue(all(r['status']=='planned' and 'episode_deadline_unix' not in r for r in b.rows))
                    return False
                return True
            b.resource_admission=admit
            def run(row,gpu):row.update(status='failed',task_success=False)
            b.run_one=Mock(side_effect=run);b.run()
            self.assertEqual(b.run_one.call_count,2);self.assertGreaterEqual(checks,4)

    def test_gpu_released_before_blocked_archive_and_next_fifo_task_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            b=self.runner(tmp);first_archive=threading.Event();next_task=threading.Event()
            def run(row,gpu):
                if row['index']==1:
                    self.assertTrue(first_archive.wait(5));next_task.set()
                row.update(status='running',episode_started_at_unix=time.time(),execution_finished_at='now',
                           simulator_cleanup={'status':'passed'},task_success=True)
                return Path(tmp),Path(tmp)
            def archive(row,*args):
                if row['index']==0:
                    first_archive.set();self.assertTrue(next_task.wait(5))
                    # The next task acquired the same real GPU/host lease while
                    # this CPU archive was still blocked.
                row.update(status='passed',episode_outcome='success')
            b.run_one=run;b.finalize_episode=archive;b.run()
            self.assertTrue(next_task.is_set());self.assertTrue(all(r['status']=='passed' for r in b.rows))

    def test_real_controller_process_stops_at_shared_execution_deadline(self):
        with tempfile.TemporaryDirectory() as tmp:
            b=self.runner(tmp);output=Path(tmp)/'controller';output.mkdir()
            # A real non-yielding CPU action stands in for a long GPU call. The
            # child process is its own model process group and is owned by path.
            child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)',str(output)],start_new_session=True)
            (output/'controller.json').write_text(json.dumps({'pid':child.pid}))
            deadline=time.time()+.15
            time.sleep(max(0,deadline-time.time()))
            b.stop_controller(child,output)
            self.assertIsNotNone(child.poll());self.assertLess(time.time()-deadline,2)

    def test_score_survives_media_error_and_late_score_cannot_change_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            b=self.runner(tmp);b._config.update(id='a',ssh=['ssh','host'])
            row=b.rows[0];row.update(execution_finished_at='now',controller_timeout=True)
            def capture(row,path):row.update(task_success=True,q_score=1,execution_finished_at_unix=100,
                                             deadline_expired_at_finish=False)
            b.capture_result=capture;b.archive=Mock(side_effect=TimeoutError('large video copy'))
            b.finalize_episode(row,Path(tmp),Path(tmp),b._config)
            self.assertEqual(row['episode_outcome'],'success');self.assertEqual(row['archive_status'],'failed')
            late={**row,'deadline_expired_at_finish':True}
            self.assertEqual(classify_episode_outcome(late),'timeout');self.assertEqual(late['q_score'],1)

    def test_restart_archive_pending_never_relaunches_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            b=self.runner(tmp,1);row=b.rows[0]
            row.update(status='running',execution_finished_at='now',simulator_cleanup={'status':'passed'},
                       worker_id='a',controller_output=tmp,simulator_output=tmp)
            b.run_one=Mock();b.adopt_one=Mock()
            b.finalize_episode=lambda row,*args:row.update(status='failed',episode_outcome='failure')
            b.run();b.run_one.assert_not_called();b.adopt_one.assert_not_called()
            self.assertEqual(row['status'],'failed')

    def test_static_host_failure_does_not_fabricate_task_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            b=self.runner(tmp);b.resource_admission=Mock(side_effect=RuntimeError('Permanent admission error: fixture'))
            b.run_one=Mock()
            with self.assertRaisesRegex(RuntimeError,'No usable worker'):b.run()
            b.run_one.assert_not_called()
            self.assertTrue(all(row['status']=='planned' for row in b.rows))

    def test_recovered_real_score_replaces_provisional_cleanup_outcome(self):
        with tempfile.TemporaryDirectory() as tmp:
            b=self.runner(tmp);b._config['sim_python']='/fixture/python'
            row=b.rows[0];row.update(status='failed',episode_outcome='failure',outcome_provisional=True,
                execution_finished_at='now',termination_reason='goal_not_satisfied')
            raw={'task_success':True,'execution_finished_at_unix':100,'deadline_expired_at_finish':False,
                 'evaluation':{'official_metrics':{'q_score':{'final':1}}}}
            b.ssh=lambda *args,**kw:subprocess.CompletedProcess([],0,json.dumps(raw),'')
            b.capture_result(row,Path(tmp));b.complete_row(row)
            self.assertEqual(row['episode_outcome'],'success');self.assertEqual(row['q_score'],1)

    def test_real_clock_command_and_delayed_model_start_exclude_initialization(self):
        with tempfile.TemporaryDirectory() as tmp:
            b=self.runner(tmp,1);b.source=Path(__file__).resolve().parents[1]
            b._config.update(id='a',gpu=0,ssh=['/bin/bash','-c'],sim_python=sys.executable)
            clock=Path(tmp)/'remote run'/"execution clock'file.json"
            command=b.execution_clock_command(clock,.15)
            # Delayed initialization is longer than this test's model budget.
            init_started=time.time();time.sleep(.2)
            first=json.loads(subprocess.check_output(command,text=True))
            second=json.loads(subprocess.check_output(command,text=True))
            self.assertEqual(first,second)  # Restart never extends the clock.
            self.assertGreater(first['execution_started_at_unix']-init_started,.15)
            self.assertAlmostEqual(first['episode_deadline_unix']-first['execution_started_at_unix'],.15,places=3)
            output=Path(tmp)/'controller';output.mkdir()
            (output/'controller.json').write_text(json.dumps(first))
            row=b.rows[0];row['startup_deadline_unix']=init_started+10
            b.controller_metadata(row,output)
            self.assertEqual(row['episode_deadline_unix'],first['episode_deadline_unix'])
            self.assertEqual(row['budget_basis'],'model_execution_excludes_initialization')

    def test_startup_watchdog_is_failure_and_not_execution_timeout(self):
        row={'status':'failed','startup_timed_out':True,'termination_reason':'startup_timeout',
             'controller_timeout':True,'task_success':None}
        self.assertEqual(classify_episode_outcome(row),'failure')
        active={**row,'startup_timed_out':False,'termination_reason':'episode_deadline_exceeded'}
        self.assertEqual(classify_episode_outcome(active),'timeout')

    def test_worker_or_archive_exit_cannot_silently_lose_tasks(self):
        for phase in ('worker','archive'):
            with self.subTest(phase=phase),tempfile.TemporaryDirectory() as tmp:
                b=self.runner(tmp,1)
                if phase=='worker':
                    b.run_one=Mock(side_effect=RuntimeError('unexpected worker error'))
                    b.update=Mock(side_effect=OSError('record storage unavailable'))
                else:
                    def run(row,gpu):
                        row.update(status='running',execution_finished_at='now',simulator_cleanup={'status':'passed'})
                        return Path(tmp),Path(tmp)
                    b.run_one=run;b.finalize_episode=Mock(side_effect=RuntimeError('archive worker crashed'))
                with self.assertRaisesRegex(RuntimeError,'Incomplete batch coverage'):b.run()
                self.assertNotIn(b.rows[0]['status'],{'passed','failed'})

    def test_manifest_rejects_missing_duplicate_or_changed_task_identity(self):
        rows=[{'index':0,'run_id':'first','task':'radio'},{'index':1,'run_id':'second','task':'water'}]
        validate_manifest_coverage(rows,2,{0:'radio',1:'water'})
        with self.assertRaisesRegex(ValueError,'coverage'):validate_manifest_coverage(rows,100)
        with self.assertRaisesRegex(ValueError,'duplicate'):validate_manifest_coverage([rows[0],rows[0]])
        with self.assertRaisesRegex(ValueError,'identities'):validate_manifest_coverage(rows,2,{0:'radio',1:'other'})

    def test_ambiguous_remote_launch_still_attempts_owned_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            b=self.runner(tmp,1)
            for d in ('logs','preflight','launchers'):(Path(tmp)/d).mkdir()
            b.source=Path('/source');b.source_info={'commit':'fixture'}
            b._config.update(id='a',gpu=0,ssh=['ssh','host'],base_port=31000,batch_tag='test',
                             data_root='/fixture',sim_python='/fixture/python',sim_env='/fixture/env')
            b.manifest={};b.unit_state=Mock(return_value={'ActiveState':'inactive','MainPID':'0'})
            def ssh(args,**kwargs):
                if 'assets' in args:return subprocess.CompletedProcess(args,0,'{"status":"passed"}','')
                if args[0]=='test':return subprocess.CompletedProcess(args,1,'','')
                if args[0]=='systemd-run':raise subprocess.TimeoutExpired(args,40)
                raise AssertionError(args)
            b.ssh=ssh;b._worker_local.defer_archive=True
            b.cleanup_owned_unit=Mock(side_effect=lambda row:row.update(simulator_cleanup={'status':'blocked','reason':'SSH unavailable'}))
            b.run_one(b.rows[0],0)
            b.cleanup_owned_unit.assert_called_once()
            self.assertIn('startup_deadline_unix',b.rows[0])
            self.assertNotIn('episode_deadline_unix',b.rows[0])
            self.assertIn('simulator_started_at_unix',b.rows[0])
            self.assertEqual(b.rows[0]['simulator_cleanup']['status'],'blocked')

if __name__=='__main__':unittest.main()
