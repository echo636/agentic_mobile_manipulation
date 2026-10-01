import importlib.util
import json
from pathlib import Path
import queue
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

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

    def test_concurrent_workers_isolate_hosts_and_claim_each_task_once(self):
        with tempfile.TemporaryDirectory() as d:
            b=batch.Batch.__new__(batch.Batch)
            b._config=self.config();b._worker_local=threading.local();b.workers=batch.worker_configs(b._config)
            b.root=Path(d);b.rows=[{'index':i,'status':'planned'} for i in range(12)]
            b.queue=queue.Queue();b.publish=lambda:None;b.journal=lambda m:None
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
            b.run_one=Mock();b.run();b.run_one.assert_not_called();self.assertEqual(b.rows[0]['status'],'planned')


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
