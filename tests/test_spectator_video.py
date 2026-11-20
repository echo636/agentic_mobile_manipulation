import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone

from manipulation_agent.spectator_video import export_spectator_video, export_spectator_walltime
from manipulation_agent.streaming_video import file_hash


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'ffmpeg/ffprobe required')
class FullSpectatorTests(unittest.TestCase):
    def test_full_spectator_export_preserves_all_frames_and_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'episode.mp4'
            # A changing pattern across a five-camera layout tests the actual
            # last-row crop, not just the presence of a generated filename.
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                'testsrc2=size=128x272:rate=12', '-t', '2', '-c:v', 'libx264',
                '-threads', '1', '-movflags', '+frag_keyframe+empty_moov+default_base_moof',
                str(source)], check=True)
            before = file_hash(source)
            (root / 'video.json').write_text(json.dumps({'status': 'passed', 'file': source.name,
                'views': ['front', 'back', 'left', 'right', 'spectator'], 'width': 128,
                'height': 272, 'frame_count': 24, 'duration_seconds': 2, 'fps': 12,
                'time_basis': 'simulation steps'}))
            record = export_spectator_video(root)
            self.assertEqual(record['status'], 'passed')
            self.assertEqual(record['crop'], [0, 128, 64, 64])
            self.assertEqual(record['frame_count'], 24)
            self.assertEqual(file_hash(source), before)
            def decode(path, crop=None):
                cmd = ['ffmpeg', '-v', 'error', '-i', str(path)]
                if crop:cmd += ['-vf', crop]
                return subprocess.check_output(cmd + ['-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'])
            expected = decode(source, 'crop=64:64:0:128')
            actual = decode(root / record['file'])
            self.assertEqual(len(expected), len(actual))
            self.assertLess(sum(abs(a-b) for a,b in zip(expected, actual))/len(actual), 4)
            self.assertEqual(export_spectator_video(root), record)
            controller = root / 'controller'; controller.mkdir()
            iso = lambda seconds: datetime.fromtimestamp(seconds, timezone.utc).isoformat()
            (root / 'run.json').write_text(json.dumps({'execution_started_at_unix': 100}))
            (controller / 'controller.json').write_text(json.dumps({'started_at': iso(100), 'finished_at': iso(106)}))
            (root / 'video_frames.jsonl').write_text(''.join(
                json.dumps({'at': iso(100+i*.2), 'frame_index': i})+'\n' for i in range(24)))
            wall = export_spectator_walltime(root, controller, fps=10)
            self.assertAlmostEqual(wall['duration_seconds'], 6, places=2)
            self.assertEqual(wall['frame_count'], 60)
            self.assertTrue(wall['model_wait_time_included'])
            self.assertEqual(wall['source_frames_displayed'], 24)
            frames = decode(root / wall['file']); size = 64 * 64 * 3
            self.assertEqual(len(frames), size * 60)
            # The final 1.4s wait must hold the last real camera frame.
            last = actual[-size:]
            held = frames[-size:]
            self.assertLess(sum(abs(a-b) for a,b in zip(last, held))/size, 4)
            self.assertEqual(file_hash(source), before)

    def test_incomplete_source_cannot_be_labeled_complete(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'video.json').write_text(json.dumps({'status': 'running', 'views': ['spectator']}))
            with self.assertRaises(ValueError):export_spectator_video(root)
            self.assertFalse((root / 'spectator.mp4').exists())


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'ffmpeg/ffprobe required')
class WristCameraExportTests(unittest.TestCase):
    def test_seven_view_recording_and_wrist_crops_keep_every_source_frame(self):
        import numpy as np
        from manipulation_agent.video import EpisodeVideo
        from manipulation_agent.spectator_video import export_camera_video
        views=('front','back','left','right','left_wrist','right_wrist','spectator')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            recorder=EpisodeVideo(root,fps=12,size=64,views=views)
            for step in range(1,4):
                pixels={view:np.full((64,64,3),[20+20*i,30+step*20,180-i*15],dtype=np.uint8)
                        for i,view in enumerate(views)}
                recorder.append(pixels,step,'env_step')
            manifest=recorder.finish(3)
            self.assertEqual(manifest['status'],'passed')
            original_hash=file_hash(root/'episode.mp4')
            for view,crop in [('left_wrist',[0,128,64,64]),('right_wrist',[64,128,64,64]),('spectator',[0,192,64,64])]:
                record=export_camera_video(root,view)
                self.assertEqual(record['crop'],crop)
                self.assertEqual(record['frame_count'],3)
                self.assertEqual(record['model_visible'],view!='spectator')
                self.assertTrue(record['checks']['every_source_frame_exported'])
                x,y,w,h=crop
                def decode(path,filters=None):
                    args=['ffmpeg','-v','error','-threads','1','-i',str(path)]
                    if filters:args+=['-vf',filters]
                    return subprocess.check_output(args+['-threads','1','-f','rawvideo','-pix_fmt','rgb24','-'])
                expected=decode(root/'episode.mp4',f'crop={w}:{h}:{x}:{y}')
                actual=decode(root/record['file'])
                self.assertEqual(len(expected),len(actual))
                self.assertLess(sum(abs(a-b) for a,b in zip(expected,actual))/len(actual),5)
            self.assertEqual(file_hash(root/'episode.mp4'),original_hash)
