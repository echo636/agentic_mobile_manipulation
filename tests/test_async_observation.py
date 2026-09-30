import concurrent.futures
import json
import math
from pathlib import Path
import tempfile
import unittest

from manipulation_agent.observations.mock_rgb import MockRGBBackend
from manipulation_agent.observations.rig import camera_mount, DIRECTIONS
from manipulation_agent.observations.boundary import public_observation
from manipulation_agent.records import Recorder
from manipulation_agent.vision_harness import VisionHarness


class AsyncObservationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)/'episode'
        self.backend = MockRGBBackend(self.root)
        self.h = VisionHarness(self.backend, Recorder(self.root, {'backend':'mock','observation_mode':'rgb_only'}))
        self.calls = 0

    def call(self, name, **args):
        self.calls += 1
        return self.h.call(name, args, f'request-{self.calls}')

    def start(self):
        return self.call('start_observation')['job']['job_id']

    def done(self):
        job = self.start(); self.h.tick_background(); self.h.tick_background()
        return self.call('get_observation', job_id=job)

    def test_start_returns_before_capture_and_tools_are_available(self):
        count = self.backend.capture
        job = self.start()
        self.assertEqual(self.backend.capture, count)
        self.assertEqual(self.call('get_observation',job_id=job)['job']['status'],'planned')
        self.h.tick_background()
        self.assertEqual(self.call('get_observation',job_id=job)['job']['status'],'running')
        self.assertTrue(self.call('list_skills')['ok'])
        self.h.tick_background()
        result = self.call('get_observation',job_id=job)
        self.assertEqual(result['job']['status'],'passed')
        self.assertFalse(result['job']['stale'])
        self.assertEqual(self.backend.steps,0); self.assertEqual(self.h.actions,0)
        self.assertEqual([i['view'] for i in result['observation']['images']],list(DIRECTIONS))
        events = [json.loads(x) for x in (self.root/'events.jsonl').read_text().splitlines()]
        ack = next(i for i,e in enumerate(events) if e['kind']=='tool_result' and e['name']=='start_observation')
        begun = next(i for i,e in enumerate(events) if e['kind']=='observation_started')
        self.assertLess(ack,begun)

    def test_cancel_before_capture_and_repeated_cancel_are_safe(self):
        job = self.start(); count = self.backend.capture
        self.call('cancel_observation',job_id=job); self.h.tick_background()
        result = self.call('get_observation',job_id=job)
        self.assertEqual(result['job']['status'],'cancelled'); self.assertNotIn('observation',result)
        self.assertEqual(self.backend.capture,count); self.assertEqual(self.backend.steps,0)
        self.assertEqual(self.call('cancel_observation',job_id=job)['job']['status'],'cancelled')

    def test_duplicate_request_never_queues_another_capture(self):
        a = self.h.call('start_observation',{},'same')
        b = self.h.call('start_observation',{},'same')
        self.assertEqual(a,b); self.assertEqual(len(self.h.surround.jobs),1)

    def test_single_pending_job_and_unknown_id(self):
        self.start()
        self.assertEqual(self.call('start_observation')['error']['code'],'observation_busy')
        self.assertEqual(self.call('get_observation',job_id='missing')['error']['code'],'unknown_observation_job')

    def test_motion_before_capture_is_allowed_and_capture_uses_new_state(self):
        job = self.start()
        r = self.call('act',primitive='wait',target=None,revision=0)
        self.assertTrue(r['ok'])
        self.h.tick_background(); self.h.tick_background()
        obs = self.call('get_observation',job_id=job)['observation']
        self.assertEqual(obs['revision'],1); self.assertEqual(obs['capture']['sim_step'],1)

    def test_new_capture_marks_cached_job_stale_and_rejects_old_pixels(self):
        old = self.done(); frame = old['observation']['images'][0]
        self.call('observe')
        stale = self.call('get_observation',job_id=old['job']['job_id'])
        self.assertTrue(stale['job']['stale'])
        r = self.call('act',primitive='grasp',revision=old['observation']['revision'],
                      target={'image_ref':frame['image_ref'],'point':[.5,.5]})
        self.assertEqual(r['error']['code'],'stale_image_ref'); self.assertEqual(self.backend.steps,0)

    def test_side_camera_pixel_is_a_valid_current_action_target(self):
        r = self.done(); frame = next(i for i in r['observation']['images'] if i['view']=='back')
        action = self.call('act',primitive='toggle_on',revision=r['observation']['revision'],
                           target={'image_ref':frame['image_ref'],'point':[.5,.5]})
        self.assertTrue(action['ok'])

    def test_failure_is_terminal_without_private_error_leak(self):
        job = self.start()
        original = self.backend.observe
        def fail(): raise RuntimeError('PRIVATE_WORLD_OBJECT_NAME')
        self.backend.observe = fail
        self.h.tick_background(); self.h.tick_background()
        r = self.call('get_observation',job_id=job)
        self.assertEqual(r['job']['status'],'failed')
        self.assertNotIn('PRIVATE_WORLD_OBJECT_NAME',json.dumps(r)); self.assertFalse(self.h.surround.active)
        self.backend.observe = original
        self.assertEqual(self.done()['job']['status'],'passed')

    def test_finish_cancels_pending_job_without_capture(self):
        job = self.start(); count = self.backend.capture
        self.call('finish',outcome='aborted',reason='Observation contract fixture')
        self.assertEqual(self.h.surround.jobs[job]['status'],'cancelled')
        self.assertEqual(self.backend.capture,count)

    def test_background_render_cannot_run_on_worker_thread(self):
        self.start()
        with concurrent.futures.ThreadPoolExecutor(1) as pool:
            with self.assertRaisesRegex(RuntimeError,'owner thread'):
                pool.submit(self.h.tick_background).result()

    def test_capture_metadata_cannot_smuggle_world_truth_or_missing_view(self):
        obs = self.backend.observe(); obs['capture']['object_positions'] = [1,2,3]
        with self.assertRaises(RuntimeError): public_observation(obs,0)
        obs = self.backend.observe(); obs['images'].pop()
        with self.assertRaises(RuntimeError): public_observation(obs,0)

    def test_fixed_camera_axes_are_cardinal_and_orthonormal(self):
        expected={'front':(1,0),'back':(-1,0),'left':(0,1),'right':(0,-1)}
        for direction in DIRECTIONS:
            pos,rotation=camera_mount(direction,1.6)
            cols=list(zip(*rotation)); forward=[-v for v in cols[2]]
            yaw=math.atan2(forward[1],forward[0])
            self.assertAlmostEqual(math.cos(yaw),expected[direction][0])
            self.assertAlmostEqual(math.sin(yaw),expected[direction][1])
            self.assertLess(forward[2],0)
            self.assertAlmostEqual(pos[2],1.6)
            for i in range(3):
                for j in range(3):self.assertAlmostEqual(sum(a*b for a,b in zip(cols[i],cols[j])),float(i==j))


if __name__ == '__main__': unittest.main()
