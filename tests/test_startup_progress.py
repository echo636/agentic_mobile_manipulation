import importlib.util
import errno
import io
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import sys
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

from manipulation_agent.startup_progress import (
    STARTUP_POLICY, startup_event, startup_stage, startup_progress_update,
    instrument_startup_calls,
)

spec=importlib.util.spec_from_file_location('startup_batch',Path(__file__).resolve().parents[1]/'scripts/run_behavior100.py')
batch=importlib.util.module_from_spec(spec);spec.loader.exec_module(batch)


class StartupProgressTests(unittest.TestCase):
    def test_transient_nas_failure_retries_without_changing_milestone_time(self):
        original = Path.open
        failures = [True]
        def open_once(path, *args, **kwargs):
            if path.name == 'startup_stages.jsonl' and failures:
                failures.pop()
                raise OSError(errno.EIO, 'temporary NAS failure')
            return original(path, *args, **kwargs)
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(Path, 'open', open_once), \
                    patch('manipulation_agent.startup_progress.time.sleep'), \
                    patch('manipulation_agent.startup_progress.time.time', return_value=123.):
                event = startup_event(tmp, 'object_construct', 'passed')
            records = [json.loads(s) for s in (Path(tmp) / 'startup_stages.jsonl').read_text().splitlines() if s]
            self.assertEqual(records, [event])
            self.assertEqual(event['at_unix'], 123.)
            self.assertEqual(event['storage_retries'], 1)

    def test_exhausted_advisory_log_io_does_not_replace_native_outcome(self):
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(Path, 'open', side_effect=OSError(errno.EIO, 'NAS unavailable')), \
                patch('manipulation_agent.startup_progress.time.sleep'), \
                patch('sys.stderr', stderr):
            with self.assertRaisesRegex(ValueError, 'native failure'):
                with startup_stage(tmp, 'native_task'):
                    raise ValueError('native failure')
        warnings = [json.loads(s) for s in stderr.getvalue().splitlines()]
        self.assertEqual([w['milestone']['status'] for w in warnings], ['running', 'failed'])
        self.assertTrue(all(w['event'] == 'startup_log_write_failed' for w in warnings))

    def test_nontransient_startup_storage_failure_is_not_hidden(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(Path, 'open', side_effect=PermissionError(errno.EACCES, 'bad output permissions')):
            with self.assertRaises(PermissionError):
                startup_event(tmp, 'native_task', 'running')

    def test_ready_bridge_wins_over_expired_idle_clock_but_unready_still_expires(self):
        b = batch.Batch.__new__(batch.Batch)
        b.manifest = {'agent_profile': 'skills'}
        b.refresh_startup_progress = Mock()
        b.initializing_unit_state = Mock()
        b.update = lambda row, **kw: row.update(kw)
        ready = {'ready': True, 'closed': False, 'tools': batch.tool_specs('skills')}
        b.health = Mock(return_value=ready)
        row = self.row()
        row['startup_deadline_unix'] = 0.
        self.assertIs(b.wait_simulator_ready(row, 'owned.service', 1), ready)
        self.assertNotIn('startup_timed_out', row)
        b.health.return_value = None
        with self.assertRaises(TimeoutError):
            b.wait_simulator_ready(row, 'owned.service', 1)
        self.assertTrue(row['startup_timed_out'])

    def test_native_progress_preserves_return_values_and_restores_on_failure(self):
        class Loader:
            def load(self, name, *, fail=False):
                if fail:
                    raise ValueError('native failure')
                return token
        token = object()
        original = Loader.load
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'native failure'):
                with instrument_startup_calls(tmp, [
                    (Loader, 'load', lambda obj, name, **kw: 'native_' + name)
                ]):
                    self.assertIs(Loader().load('chair'), token)
                    Loader().load('table', fail=True)
            self.assertIs(Loader.load, original)
            events = [json.loads(s) for s in (Path(tmp) / 'startup_stages.jsonl').read_text().splitlines()]
            self.assertEqual([(e['stage'], e['status']) for e in events], [
                ('native_chair', 'running'), ('native_chair', 'passed'),
                ('native_table', 'running'), ('native_table', 'failed')])

    def test_native_object_progress_spans_long_constructor_but_repeats_expire(self):
        row = self.row()
        row['startup_timeout_seconds'] = 1200.
        row['startup_deadline_unix'] = 1300.
        events = [dict(stage=stage, status='passed', at_unix=at) for stage, at in [
            ('native_object_chair_load', 1000.),
            ('native_object_table_load', 2100.),
            ('construct_evaluator_and_environment', 2400.)]]
        row.update(startup_progress_update(row, events, 2401.))
        self.assertEqual(row['startup_deadline_unix'], 3600.)
        self.assertEqual(startup_progress_update(row, [
            dict(stage='native_object_table_load', status='passed', at_unix=3500.)], 3501.), {})

    def test_repeated_objects_in_distinct_native_passes_renew_but_same_pass_does_not(self):
        clock = [200.]
        class Loader:
            def restore(self, name):
                return name
            def run(self, phase, times):
                for name, at in zip(('chair', 'table'), times):
                    clock[0] = at
                    self.restore(name)
        row = self.row()
        row.update(startup_timeout_seconds=1000., startup_deadline_unix=1100.)
        with tempfile.TemporaryDirectory() as tmp, \
                patch('manipulation_agent.startup_progress.time.time', side_effect=lambda: clock[0]):
            with instrument_startup_calls(tmp, [
                (Loader, 'run', lambda obj, phase, times: 'native_' + phase),
                (Loader, 'restore', lambda obj, name: 'object_' + name + '_restore'),
            ]):
                loader = Loader()
                loader.run('load', (300., 400.))
                clock[0] = 800.
                loader.run('reset', (1500., 2200.))
                path = Path(tmp) / 'startup_stages.jsonl'
                events = [json.loads(s) for s in path.read_text().splitlines()]
                row.update(startup_progress_update(row, events, 2201.))
                self.assertEqual(row['startup_deadline_unix'], 3200.)
                self.assertIn('native_load/object_chair_restore:passed', row['startup_seen_milestones'])
                self.assertIn('native_reset/object_chair_restore:passed', row['startup_seen_milestones'])
                clock[0] = 2300.
                loader.run('reset', (2800., 3100.))
                repeated = [json.loads(s) for s in path.read_text().splitlines()]
                self.assertEqual(startup_progress_update(row, repeated, 3101.), {})

    def test_recursive_startup_calls_do_not_generate_unbounded_scope_keys(self):
        class Loader:
            def recurse(self, depth):
                if depth:
                    self.recurse(depth - 1)
                return self.leaf()
            def leaf(self):
                return 'loaded'
        with tempfile.TemporaryDirectory() as tmp:
            with instrument_startup_calls(tmp, [
                (Loader, 'recurse', lambda *a: 'native_recurse'),
                (Loader, 'leaf', lambda *a: 'native_leaf'),
            ]):
                self.assertEqual(Loader().recurse(4), 'loaded')
                self.assertEqual(Loader().leaf(), 'loaded')
            events = [json.loads(s) for s in (Path(tmp) / 'startup_stages.jsonl').read_text().splitlines()]
            self.assertEqual({e['stage'] for e in events},
                             {'native_recurse', 'native_recurse/native_leaf', 'native_leaf'})

    def test_task_snapshot_and_restore_after_scene_load_are_real_finite_progress(self):
        from manipulation_agent.omnigibson_backend import OmniGibsonBackend

        class NativeBase:
            def __getattr__(self, name):
                raise AttributeError(name)

        # Only the native call boundary is stubbed. Exercise the adapter's
        # actual registration, watchdog transition and restoration of methods.
        for name in ('load', 'prebuild', 'initialize', 'reset', 'save',
                     'update_initial_file', '_load_state', 'dump_state',
                     '_load_variables', '_load_scene', '_load_objects', '_load_robots',
                     '_load_task', '_load_external_sensors', 'post_play_load',
                     'load_observation_space', '_load_action_space', 'get_obs',
                     'update_activity', 'initialize_activity'):
            setattr(NativeBase, name, lambda *a, **kw: token)
        token = object()
        Scene, Object, Environment, Task = [type(n, (NativeBase,), {})
                                           for n in ('Scene', 'Object', 'Environment', 'Task')]
        modules = {}
        for path, attrs in {
            'omnigibson.scenes.scene_base': {'Scene': Scene, 'create_object_from_init_info': lambda info: token},
            'omnigibson.objects.usd_object': {'USDObject': Object},
            'omnigibson.prims.prim_base': {'BasePrim': NativeBase},
            'omnigibson.envs.env_base': {'Environment': Environment},
            'omnigibson.tasks.behavior_task': {'BehaviorTask': Task},
        }.items():
            module = ModuleType(path)
            module.__dict__.update(attrs)
            modules[path] = module
        scenes = ModuleType('omnigibson.scenes')
        scenes.scene_base = modules['omnigibson.scenes.scene_base']
        modules['omnigibson.scenes'] = scenes
        obj = Object(); obj.name = 'chair'
        with tempfile.TemporaryDirectory() as tmp, patch.dict(sys.modules, modules):
            backend = OmniGibsonBackend.__new__(OmniGibsonBackend)
            backend.output = Path(tmp)
            row = self.row()
            row.update(startup_timeout_seconds=1200., startup_deadline_unix=1300.)
            # This previously invisible tail lasts 34 minutes after scene
            # initialization. Each event is emitted by a real call boundary.
            with backend._native_startup_progress():
                for at, call in [(1000., Scene().initialize),
                                 (2000., Environment()._load_task),
                                 (3000., obj.dump_state)]:
                    with patch('manipulation_agent.startup_progress.time.time', return_value=at):
                        self.assertIs(call(), token)
            with backend._native_startup_progress('instance_reset'):
                with patch('manipulation_agent.startup_progress.time.time', return_value=4000.):
                    self.assertIs(obj._load_state(), token)
            events = [json.loads(s) for s in (Path(tmp) / 'startup_stages.jsonl').read_text().splitlines()]
            row.update(startup_progress_update(row, events, 4001.))
            self.assertEqual(row['startup_deadline_unix'], 5200.)
            self.assertEqual(startup_progress_update(row, events, 5199.), {})
            self.assertNotIn('load', vars(Task))
            self.assertNotIn('dump_state', vars(Object))
            self.assertNotIn('episode_deadline_unix', row)

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

    def test_slow_camera_substages_renew_only_for_real_new_progress(self):
        row = self.row()
        events = [
            {'stage': 'surround_camera_setup_and_warmup', 'status': 'running', 'at_unix': 200.},
            {'stage': 'surround_camera_front_initialize', 'status': 'running', 'at_unix': 201.},
            {'stage': 'surround_camera_front_initialize', 'status': 'passed', 'at_unix': 1700.},
            {'stage': 'surround_camera_back_initialize', 'status': 'running', 'at_unix': 1701.},
            {'stage': 'surround_camera_back_initialize', 'status': 'passed', 'at_unix': 2400.},
            {'stage': 'surround_camera_warmup_frame_01', 'status': 'running', 'at_unix': 2401.},
        ]
        row.update(startup_progress_update(row, events, 2402.))
        # The whole camera stage exceeds 30 minutes, but completed work renewed it.
        self.assertEqual(row['startup_deadline_unix'], 4201.)
        # A render stuck on frame 1 still expires; rereading or a late completion
        # cannot fabricate progress or restart the model execution clock.
        self.assertEqual(startup_progress_update(row, events, 4202.), {})
        self.assertEqual(startup_progress_update(row, [{
            'stage': 'surround_camera_warmup_frame_01', 'status': 'passed',
            'at_unix': 4202.,
        }], 4203.), {})
        self.assertNotIn('execution_started_at_unix', row)

    def test_nested_camera_failure_identifies_substage_without_success_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, 'renderer'):
                with startup_stage(tmp, 'surround_camera_setup_and_warmup'):
                    with startup_stage(tmp, 'surround_camera_front_load'):
                        pass
                    with startup_stage(tmp, 'surround_camera_front_initialize'):
                        raise RuntimeError('renderer')
            events = [json.loads(s) for s in (Path(tmp) / 'startup_stages.jsonl').read_text().splitlines()]
            self.assertEqual([(e['stage'], e['status']) for e in events[-2:]], [
                ('surround_camera_front_initialize', 'failed'),
                ('surround_camera_setup_and_warmup', 'failed'),
            ])
            self.assertNotIn(('surround_camera_front_initialize', 'passed'),
                             [(e['stage'], e['status']) for e in events])

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
