"""Real blocked encoder pipe must not prevent task scoring or episode closure."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import errno
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from manipulation_agent.reporting import video
from manipulation_agent.runtime.deadline import EpisodeDeadline
from manipulation_agent.observations.mock_rgb import MockRGBBackend
from manipulation_agent.records import Recorder
from manipulation_agent.runtime.vision_harness import VisionHarness
from manipulation_agent.executors.profiling import action_profile
from manipulation_agent.contracts import SkillError


class VideoFailureIsolationTests(unittest.TestCase):
    def test_timeline_eio_and_failed_failure_manifest_do_not_prevent_scoring(self):
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'episode';recorder=Recorder(output,{})
            backend=MockRGBBackend(output)
            harness=VisionHarness(backend,recorder,profile='minimal')
            views=('front','back','left','right','spectator')
            recording=video.EpisodeVideo(output,30,size=32,views=views)
            backend.finalize_video=lambda:recording.finish(backend.steps)
            pixels={v:np.zeros((32,32,3),dtype=np.uint8) for v in views}
            original_open=Path.open
            def faulty_open(path,*args,**kwargs):
                if path.name == 'video_frames.jsonl' or (path.name.startswith('video.json.') and path.suffix == '.tmp'):
                    raise OSError(errno.EIO,'simulated NAS I/O error',str(path))
                return original_open(path,*args,**kwargs)
            with patch.object(Path,'open',faulty_open):
                recording.append(pixels,0,'observation')
                self.assertTrue(recording.closed)
                self.assertEqual(recording.manifest['status'],'failed')
                self.assertIn('failure_manifest_write_error',recording.manifest)
                self.assertIsNotNone(recording.process.poll())
                backend.on=True
                harness.call('finish',{'outcome':'achieved','reason':'fixture'},'finish')
                self.assertTrue(recorder.run['task_success'])
                self.assertEqual(recorder.run['scoring']['status'],'passed')
            harness.finalize_recording()
            run=json.loads((output/'run.json').read_text())
            self.assertTrue(run['task_success'])
            self.assertEqual(run['video']['status'],'failed')
            self.assertEqual(run['video']['failure_type'],'OSError')

    def test_timing_eio_preserves_primary_failure_and_success(self):
        @action_profile
        def execute(backend,primitive):
            if primitive=='close':raise SkillError('postcondition_error','original failure')
            return {'effect':'executed'}
        with tempfile.TemporaryDirectory() as tmp:
            for primitive in ('close','open'):
                backend=SimpleNamespace(output=Path(tmp))
                with patch.object(Path,'open',side_effect=OSError(errno.EIO,'NAS unavailable')):
                    if primitive=='close':
                        with self.assertRaisesRegex(SkillError,'original failure'):
                            execute(backend,primitive)
                    else:self.assertEqual(execute(backend,primitive),{'effect':'executed'})
                self.assertEqual(backend.diagnostic_write_failures[0]['artifact'],'performance.jsonl')

    def test_real_pipe_stall_isolated_and_stop_interrupts_bounded_backpressure(self):
        real_popen = subprocess.Popen
        for stop in (False, True):
            with self.subTest(stop=stop), tempfile.TemporaryDirectory() as tmp:
                output=Path(tmp)/'episode';recorder=Recorder(output,{})
                backend=MockRGBBackend(output);backend.deadline=EpisodeDeadline()
                harness=VisionHarness(backend,recorder,profile='minimal')
                views=('front','back','left','right','spectator')
                recording=video.EpisodeVideo(output,30,size=32,views=views)
                backend.finalize_video=lambda:recording.finish(backend.steps)
                pixels={v:np.zeros((32,32,3),dtype=np.uint8) for v in views}
                children=[];timers=[];owner=threading.get_ident();callback_threads=[]
                encoder_startup_seconds=0.

                def stalled_encoder(command,**kwargs):
                    nonlocal encoder_startup_seconds
                    # Accept the first bytes, preserve an output fragment, then
                    # stop draining stdin. No simulator dependency or fake Future.
                    script="from pathlib import Path; import os,sys,time; Path(sys.argv[1]).write_bytes(b'partial-video'); os.read(0,1); time.sleep(30)"
                    started=time.monotonic()
                    child=real_popen([sys.executable,'-I','-S','-c',script,command[-1]],**kwargs)
                    children.append(child)
                    # Exercise a running encoder blocked on stdin, rather than
                    # racing cancellation against Python/site startup on NAS.
                    deadline=started+5
                    while not Path(command[-1]).exists() and child.poll() is None and time.monotonic()<deadline:
                        time.sleep(.01)
                    self.assertTrue(Path(command[-1]).exists(),'Fixture encoder did not become ready')
                    encoder_startup_seconds+=time.monotonic()-started
                    return child

                def check_active():
                    callback_threads.append(threading.get_ident())
                    backend.deadline.check(changed=True)

                def execute(*args,**kwargs):
                    backend.on=True
                    for i in range(12):
                        backend.steps+=1
                        if stop and i==8:
                            timer=threading.Timer(.03,backend.deadline.request_stop)
                            timer.start();timers.append(timer)
                        recording.append(pixels,backend.steps,'env_step',check_active=check_active)
                    return {}

                backend.execute_visual=execute
                try:
                    with patch.object(video.subprocess,'Popen',side_effect=stalled_encoder), \
                         patch.object(video,'ffmpeg_executable',return_value='fixture-encoder'), \
                         patch.object(video,'ENCODER_STALL_SECONDS',.3):
                        started=time.monotonic()
                        action=harness.call('act',{'primitive':'toggle_on',
                            'target':{'image_ref':harness.snapshot['images'][0]['image_ref'],'point':[.5,.5]},
                            'revision':harness.revision},'act')
                        elapsed=time.monotonic()-started
                        self.assertTrue(callback_threads)
                        self.assertEqual(set(callback_threads),{owner})
                        if stop:
                            self.assertEqual(action['error']['code'],'episode_cancelled')
                            self.assertIsNone(recording.recording_failure)  # Closure won before encoder timeout.
                        else:
                            self.assertTrue(action['ok'])
                            self.assertTrue(recording.closed)
                            self.assertLess(elapsed-encoder_startup_seconds,2)
                        finished=harness.call('finish',{'outcome':'aborted' if stop else 'achieved',
                                                       'reason':'fixture result'},'finish')
                        self.assertTrue(finished['closed'])
                        before=json.loads((output/'run.json').read_text())
                        self.assertTrue(before['task_success'])
                        self.assertEqual(before['scoring']['status'],'passed')
                        harness.finalize_recording()
                    after=json.loads((output/'run.json').read_text())
                    self.assertTrue(after['task_success'])
                    self.assertEqual(after['scoring']['status'],'passed')
                    self.assertEqual(after['video']['status'],'failed')
                    self.assertTrue(after['video']['recording_disabled'])
                    self.assertEqual(after['video']['failure_type'],'TimeoutError')
                    self.assertEqual((output/'episode.mp4').read_bytes(),b'partial-video')
                    self.assertTrue((output/'video_frames.jsonl').is_file())
                    self.assertIsNotNone(children[0].poll())
                finally:
                    for timer in timers:timer.join(1)
                    for child in children:
                        if child.poll() is None:child.kill();child.wait(timeout=2)


if __name__=='__main__':unittest.main()
