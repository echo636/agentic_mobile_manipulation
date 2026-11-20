import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from manipulation_agent.streaming_video import prepare_browser_video, packet_signature, file_hash


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'ffmpeg/ffprobe required')
class BrowserVideoTests(unittest.TestCase):
    def test_fragmented_recording_retains_every_packet_and_timestamp(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'episode.mp4'
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=64x64:rate=30',
                '-t','1','-c:v','libx264','-threads','1','-g','10','-bf','2',
                '-movflags','+frag_keyframe+empty_moov+default_base_moof',str(source)],check=True)
            before=file_hash(source);record=prepare_browser_video(root)
            self.assertTrue(record['packets_and_timestamps_unchanged'])
            self.assertEqual(record['packet_signature']['packets'],30)
            self.assertEqual(packet_signature(source),packet_signature(root/record['file']))
            self.assertEqual(file_hash(source),before)
            # The normal sample table must precede media data for browser seeks.
            payload=(root/record['file']).read_bytes()
            self.assertLess(payload.index(b'moov'),payload.index(b'mdat'))
            self.assertEqual(prepare_browser_video(root),record)
