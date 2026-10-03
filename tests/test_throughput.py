import importlib.util
import json
from pathlib import Path
import queue
import tempfile
import threading
import os
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import time

from manipulation_agent.executors.omnigibson_rgb import RGBBackend

spec = importlib.util.spec_from_file_location('throughput_batch', Path(__file__).resolve().parents[1]/'scripts/run_behavior100.py')
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)


class WorkerTests(unittest.TestCase):
    def config(self):
        return {'gpus':[1], 'ssh':['ssh','host-a'], 'base_port':29000,
                'sim_python':'/a/python', 'workers':[
                    {'id':'a','gpu':1}, {'id':'b','gpu':2,'ssh':['ssh','host-b'],'sim_python':'/b/python'}]}

    def test_duplicate_device_or_port_is_rejected(self):
        c=self.config();c['workers'][1].update(gpu=1,ssh=['ssh','host-a'])
        with self.assertRaises(ValueError):batch.worker_configs(c)
        c=self.config();c['workers'][1].update(ssh=['ssh','host-a'],base_port=28999)
        with self.assertRaises(ValueError):batch.worker_configs(c)
        c=self.config();c['workers'][0]['simulator_env']={'UNREVIEWED':'x'}
        with self.assertRaises(ValueError):batch.worker_configs(c)

    def test_legacy_single_host_config(self):
        c=self.config();del c['workers'];c['gpus']=[0,1]
        self.assertEqual([w['id'] for w in batch.worker_configs(c)],['gpu0','gpu1'])

    def test_host_memory_reservation_cannot_overcommit(self):
        c=self.config();c['workers'][1]['ssh']=['ssh','host-a']
        c['host_worker_memory_budget_gib']={'host-a':28}
        self.assertEqual(len(batch.worker_configs(c)),2)  # candidates share one host lease
        for w in c['workers']:w.update(memory_budget_gib=14,simulator_memory_max='14G')
        self.assertEqual(len(batch.worker_configs(c)),2)

    def test_concurrent_workers_isolate_hosts_and_claim_each_task_once(self):
        with tempfile.TemporaryDirectory() as d:
            b=batch.Batch.__new__(batch.Batch)
            b._config=self.config();b._worker_local=threading.local();b.workers=batch.worker_configs(b._config)
            b.root=Path(d);b.rows=[{'index':i,'status':'planned'} for i in range(12)]
            b.queue=queue.Queue();b.publish=lambda:None;b.journal=lambda m:None
            b.lock=threading.RLock();b.worker_states={};b.resource_admission=lambda:True
            b.acquire_worker_lease=lambda **kw:Mock()
            barrier=threading.Barrier(2);seen=[];lock=threading.Lock();first=set()
            def run(row,gpu):
                worker=b.c['id']
                if worker not in first:
                    first.add(worker);barrier.wait(timeout=5)
                with lock:seen.append((row['index'],worker,b.c['ssh'][-1],b.c['sim_python'],gpu))
                row['status']='passed'
            b.run_one=run;b.run()
            self.assertEqual(sorted(r[0] for r in seen),list(range(12)))
            self.assertEqual({r[1] for r in seen},{'a','b'})
            for _,worker,host,python,gpu in seen:
                self.assertEqual((host,python,gpu),('host-a','/a/python',1) if worker=='a' else ('host-b','/b/python',2))
            self.assertIs(b.c,b._config)

    def test_drain_does_not_consume_queued_tasks(self):
        with tempfile.TemporaryDirectory() as d:
            b=batch.Batch.__new__(batch.Batch);b._config=self.config();b._worker_local=threading.local()
            b.workers=batch.worker_configs(b._config);b.root=Path(d)
            (b.root/'drain_requested.json').write_text('{}')
            b.rows=[{'status':'planned'}];b.queue=queue.Queue();b.publish=lambda:None;b.journal=lambda m:None
            b.lock=threading.RLock();b.worker_states={}
            b.run_one=Mock();b.run();b.run_one.assert_not_called();self.assertEqual(b.rows[0]['status'],'planned')

    def test_handoff_finalizes_original_result_without_restarting_model(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);ctrl=root/'controller';ctrl.mkdir();(root/'logs').mkdir()
            (ctrl/'controller.json').write_text(json.dumps({'started_at':datetime.now(timezone.utc).isoformat(),
                'formal_finish_observed':True,'status':'passed','pid':345,'duration_seconds':25}))
            b=batch.Batch.__new__(batch.Batch);b._config={'id':'a','ssh':['ssh','host-a'],'sim_python':'/fixture/python'};b._worker_local=threading.local()
            b.root=root;b.manifest={'model_timeout_seconds':100};b.unit_state=lambda unit:{'ActiveState':'inactive','MainPID':'0'}
            b.update=lambda row,**kw:row.update(kw);b.journal=Mock();b.ssh=Mock(return_value=SimpleNamespace(stdout='',stderr=''))
            def archive(row,*args):row.update(task_success=True,video_validation='passed',observation_validation='passed',evidence_alignment='passed')
            b.archive=archive
            row={'run_id':'original_r1','source':{'commit':'old'},'controller_output':str(ctrl),'simulator_output':'/original/run',
                 'simulator_unit':'owned.service','port':29900,'controller_wrapper_pid':999999999}
            b.adopt_one(row)
            self.assertEqual(row['status'],'passed');self.assertEqual(row['source'],{'commit':'old'})
            self.assertEqual(row['simulator_cleanup']['status'],'passed')
            self.assertEqual(row['run_id'],'original_r1');self.assertEqual(row['model_duration_seconds'],25)
            self.assertTrue(b.ssh.called)  # compact remote outcome queried before archival

    def test_handoff_rejects_unrelated_process_and_memory_mismatch(self):
        with self.assertRaises(RuntimeError):batch.controller_process_alive(os.getpid(),'/not-this-controller')
        c=self.config();c['workers'][0].update(memory_budget_gib=14,simulator_memory_max='28G')
        with self.assertRaises(ValueError):batch.worker_configs(c)

    def test_initializing_handoff_preserves_source_and_deadline(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'logs').mkdir();runtime=root/'original_runtime'
            row={'run_id':'original','source':{'commit':'old'},'controller_output':str(root/'controller'),
                 'simulator_unit':'original.service','port':29001,'instruction':'Original instruction'}
            b=batch.Batch.__new__(batch.Batch);b._worker_local=threading.local();b.root=root
            spec={'runtime':str(runtime),'deadline_unix':time.time()+10,'simulator_pid':123}
            b._config={'ssh':['ssh','original-host'],'sim_python':'/original/python','adopt_startup':{'original':spec}}
            b.manifest={'agent_profile':'skills','model':'same-model','model_timeout_seconds':1800}
            b.environment={};b.update=lambda r,**kw:r.update(kw);b.journal=Mock()
            b.unit_state=lambda unit:{'ActiveState':'active','Description':'BEHAVIOR100 owned original','MainPID':'123'}
            b.health=lambda port:{'ready':True,'closed':False,'tools':batch.tool_specs('skills')}
            with patch.object(batch.subprocess,'check_output',return_value='old\n'), patch.object(batch.subprocess,'Popen') as start:
                start.return_value.pid=987;b.start_adopted_policy(row)
                args=start.call_args.args[0]
                self.assertIn(str(runtime/'scripts/run_codex_controller.py'),args)
                self.assertEqual(args[args.index('--timeout')+1],'1800')
                self.assertEqual(start.call_args.kwargs['env']['PYTHONPATH'],str(runtime/'src'))
                self.assertEqual(row['controller_wrapper_pid'],987)
            spec['deadline_unix']=time.time()-1
            with patch.object(batch.subprocess,'check_output',return_value='old\n'), patch.object(batch.subprocess,'Popen') as start:
                with self.assertRaises(TimeoutError):b.start_adopted_policy(row)
                start.assert_not_called()


class RenderTests(unittest.TestCase):
    def test_observation_video_uses_exact_same_pixels_without_rerender(self):
        b=RGBBackend.__new__(RGBBackend);b.steps=7;b.video_render_stride=2
        b.video=SimpleNamespace(closed=False,append=Mock());b._recorded_pixels=None
        b._render_rgb_views=Mock(side_effect=AssertionError('unexpected duplicate render'))
        b._position_spectator=Mock(side_effect=AssertionError('unexpected duplicate positioning'))
        pixels={view:object() for view in ['front','back','left','right','spectator']}
        b._video_frame('observation_boundary',rendered=pixels)
        args,kwargs=b.video.append.call_args
        self.assertIs(args[0],pixels);self.assertEqual(kwargs,{'capture_env_step':7,'repeated':False})

    def test_video_flush_option_does_not_reduce_observation_barrier(self):
        import numpy as np
        b=RGBBackend.__new__(RGBBackend);b.image_size=8;b._position_rig=Mock()
        render=Mock();b.og=SimpleNamespace(sim=SimpleNamespace(render=render))
        raw=SimpleNamespace(detach=lambda:SimpleNamespace(cpu=lambda:SimpleNamespace(numpy=lambda:np.ones((8,8,4)))))
        b.rig={v:SimpleNamespace(get_obs=lambda:({'rgb':raw},{})) for v in ['front','back','left','right']}
        b._render_rgb_views(flushes=2);self.assertEqual(render.call_count,2)
        render.reset_mock();b._render_rgb_views();self.assertEqual(render.call_count,4)
        b._position_rig.assert_called()

    def test_real_encoder_preserves_hold_hashes_and_new_frames(self):
        import numpy as np
        from manipulation_agent.video import EpisodeVideo
        with tempfile.TemporaryDirectory() as d:
            out=Path(d);views=('front','back','left','right','spectator')
            v=EpisodeVideo(out,30,size=32,views=views)
            first={k:np.zeros((32,32,3),dtype=np.uint8) for k in views}
            second={k:np.full((32,32,3),100,dtype=np.uint8) for k in views}
            v.append(first,1,'env_step');v.append(first,2,'env_step',capture_env_step=1,repeated=True)
            v.append(second,3,'env_step');result=v.finish(3)
            rows=[json.loads(s) for s in (out/'video_frames.jsonl').read_text().splitlines()]
            self.assertEqual(result['status'],'passed');self.assertEqual(result['frame_count'],3)
            self.assertEqual(rows[0]['rgb_sha256'],rows[1]['rgb_sha256'])
            self.assertNotEqual(rows[1]['rgb_sha256'],rows[2]['rgb_sha256'])
            self.assertTrue(rows[1]['repeated_camera_frame']);self.assertEqual(rows[1]['camera_capture_env_step'],1)
