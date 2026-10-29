import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from manipulation_agent.startup_progress import (
    STARTUP_POLICY, startup_event, startup_stage, startup_progress_update,
)

spec=importlib.util.spec_from_file_location('startup_batch',Path(__file__).resolve().parents[1]/'scripts/run_behavior100.py')
batch=importlib.util.module_from_spec(spec);spec.loader.exec_module(batch)


class StartupProgressTests(unittest.TestCase):
    def row(self):
        return {'run_id':'fixture','startup_watchdog_policy':STARTUP_POLICY,
                'simulator_started_at_unix':100., 'startup_deadline_unix':1900.,
                'startup_timeout_seconds':1800., 'episode_timeout_seconds':1800.}

    def test_long_initialization_with_real_milestones_does_not_spend_execution_budget(self):
        row=self.row()
        events=[{'stage':'imports','status':'running','at_unix':110.},
                {'stage':'imports','status':'passed','at_unix':700.},
                {'stage':'environment','status':'running','at_unix':701.},
                {'stage':'environment','status':'passed','at_unix':1765.},
                {'stage':'cameras','status':'running','at_unix':1878.},
                {'stage':'cameras','status':'passed','at_unix':2200.}]
        row.update(startup_progress_update(row,events,2201.))
        self.assertEqual(row['startup_deadline_unix'],4000.)
        self.assertEqual(row['episode_timeout_seconds'],1800.)
        self.assertNotIn('episode_deadline_unix',row)
        # No new milestones means expiry at 4000, regardless of polling.
        self.assertEqual(startup_progress_update(row,events,3999.),{})
        self.assertEqual(startup_progress_update(row,events,4001.),{})

    def test_heartbeats_and_repeated_stage_names_cannot_keep_stuck_startup_alive(self):
        row=self.row()
        row.update(startup_progress_update(row,[{'stage':'camera','status':'running','at_unix':200.}],201.))
        for at in [500.,1000.,1800.]:
            self.assertEqual(startup_progress_update(row,[
                {'stage':'camera','status':'running','at_unix':at},
                {'stage':'heartbeat','status':'heartbeat','at_unix':at}],at),{})
        self.assertEqual(row['startup_deadline_unix'],2000.)

    def test_legacy_and_execution_clocks_are_never_extended(self):
        event={'stage':'ready','status':'passed','at_unix':1800.}
        row=self.row();row.pop('startup_watchdog_policy')
        self.assertEqual(startup_progress_update(row,[event],1801.),{})
        row=self.row();row['episode_deadline_unix']=3600.
        self.assertEqual(startup_progress_update(row,[event],1801.),{})
        self.assertEqual(row['episode_deadline_unix'],3600.)

    def test_late_future_or_malformed_events_cannot_revive_expired_stage(self):
        row=self.row()
        for event in [None,{'stage':'x','status':'passed','at_unix':1901.},
                      {'stage':'x','status':'passed','at_unix':99.},
                      {'stage':'x','status':'passed','at_unix':float('nan')},
                      {'stage':'x','status':'passed','at_unix':True},
                      {'stage':'x','status':'passed','at_unix':800.}]:
            self.assertEqual(startup_progress_update(row,[event],700.),{})

    def test_stage_record_contains_absolute_time_and_failure_is_not_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            with startup_stage(tmp,'imports'):pass
            with self.assertRaisesRegex(RuntimeError,'fixture'):
                with startup_stage(tmp,'camera'):raise RuntimeError('fixture')
            records=[json.loads(line) for line in (Path(tmp)/'startup_stages.jsonl').read_text().splitlines()]
            self.assertEqual([(r['stage'],r['status']) for r in records],
                             [('imports','running'),('imports','passed'),('camera','running'),('camera','failed')])
            self.assertTrue(all(isinstance(r['at_unix'],float) and r['at'].endswith('+00:00') for r in records))
            self.assertGreaterEqual(records[-1]['elapsed_seconds'],0)

    def test_remote_reader_uses_real_file_ignores_partial_line_and_does_not_renew_on_reread(self):
        with tempfile.TemporaryDirectory() as tmp:
            b=batch.Batch.__new__(batch.Batch);b._worker_local=threading.local()
            b.update=lambda row,**kw:row.update(kw)
            b.ssh=lambda args,**kw:subprocess.run(args,capture_output=True,text=True,timeout=kw['timeout'])
            at=batch.time.time();row=self.row()
            row.update(simulator_output=tmp,simulator_started_at_unix=at-100,startup_deadline_unix=at+1700)
            event=startup_event(tmp,'environment','passed')
            with (Path(tmp)/'startup_stages.jsonl').open('a') as f:f.write('{partial')
            b.refresh_startup_progress(row)
            self.assertAlmostEqual(row['startup_deadline_unix'],event['at_unix']+1800)
            before=row.copy();b._worker_local.startup_progress_poll=None;b.refresh_startup_progress(row)
            self.assertEqual(row,before)

    def test_controller_handshake_uses_refreshed_deadline_but_clock_metadata_takes_over(self):
        b=batch.Batch.__new__(batch.Batch);b._worker_local=threading.local()
        b.update=lambda row,**kw:row.update(kw)
        row=self.row();row['startup_deadline_unix']=101.
        process=Mock();process.poll.side_effect=[None,0];process.last_heartbeat=100.
        b.controller_metadata=Mock(return_value={})
        b.refresh_startup_progress=Mock(side_effect=lambda r:r.update(startup_deadline_unix=200.))
        b.stop_controller=Mock()
        with patch.object(batch.time,'time',return_value=150.),patch.object(batch.time,'sleep'),patch.object(batch.time,'monotonic',return_value=100.):
            b.wait_controller(row,process,Path('/fixture'))
        b.refresh_startup_progress.assert_called_once_with(row)
        b.stop_controller.assert_not_called()
        self.assertNotIn('startup_timed_out',row)

    def test_adoption_uses_renewed_new_attempt_deadline_and_keeps_legacy_cutoff(self):
        for modern in (True,False):
            with self.subTest(modern=modern),tempfile.TemporaryDirectory() as tmp:
                b=batch.Batch.__new__(batch.Batch);b.root=Path(tmp);(b.root/'logs').mkdir()
                b._worker_local=threading.local();b.environment={};b.journal=Mock()
                b._config={'ssh':['ssh','fixture'],'sim_python':'/fixture/python'}
                b.manifest={'agent_profile':'skills','model':'fixture','model_timeout_seconds':1800}
                b.update=lambda r,**kw:r.update(kw)
                row=self.row();row.update(runtime_source='/source',simulator_unit='owned',simulator_pid=123,
                    controller_output=str(b.root/'controller'),source={'commit':'fixed'},port=32000,
                    instruction='fixture',startup_deadline_unix=140.,execution_clock_path='/clock')
                if not modern:row.pop('startup_watchdog_policy')
                def progress(r):
                    if r.get('startup_watchdog_policy')==STARTUP_POLICY:r['startup_deadline_unix']=300.
                b.refresh_startup_progress=Mock(side_effect=progress)
                b.unit_state=Mock(return_value={'ActiveState':'active','MainPID':'123',
                                              'Description':'BEHAVIOR100 owned fixture'})
                b.health=Mock(return_value={'ready':True,'closed':False,'tools':batch.tool_specs('skills')})
                with patch.object(batch.time,'time',return_value=150.), \
                     patch.object(batch.subprocess,'check_output',return_value='fixed\n'), \
                     patch.object(batch.subprocess,'Popen') as launch:
                    if modern:
                        self.assertIs(b.start_adopted_policy(row),launch.return_value)
                        launch.assert_called_once()
                    else:
                        with self.assertRaisesRegex(TimeoutError,'unchanged deadline'):b.start_adopted_policy(row)
                        launch.assert_not_called()


if __name__=='__main__':unittest.main()
