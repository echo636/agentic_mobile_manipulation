"""A simulator-added empty loader path must not search the episode's cwd."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from manipulation_agent import video


@unittest.skipUnless(Path('/usr/bin/ffmpeg').exists() and shutil.which('ffprobe'),
                     'System ffmpeg and ffprobe required')
class VideoLibrarySearchTests(unittest.TestCase):
    def test_empty_loader_entries_cannot_load_libraries_from_working_directory(self):
        real_popen = subprocess.Popen
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shadow = root / 'working_directory'
            shadow.mkdir()
            # Linux FFmpeg links libm. An inherited empty path makes ld.so
            # select this invalid cwd library before the system library.
            (shadow / 'libm.so.6').write_bytes(b'not a shared library')
            output = root / 'recording'
            output.mkdir()
            views = ('front', 'back', 'left', 'right', 'spectator')
            pixels = {name: np.zeros((32, 32, 3), dtype=np.uint8) for name in views}

            def start(command, **kwargs):
                return real_popen(command, cwd=shadow, **kwargs)

            with patch.dict(os.environ, {'LD_LIBRARY_PATH': ':'}), \
                    patch.object(video, 'ffmpeg_executable', return_value='/usr/bin/ffmpeg'), \
                    patch.object(video.subprocess, 'Popen', side_effect=start):
                recording = video.EpisodeVideo(output, 30, size=32, views=views)
                recording.append(pixels, 0, 'observation')
                recording.append(pixels, 1, 'env_step')
                result = recording.finish(1)
                self.assertEqual(result['status'], 'passed')
                self.assertEqual(os.environ['LD_LIBRARY_PATH'], ':')
            probe = subprocess.run(['ffprobe', '-v', 'error', '-count_frames',
                                    '-show_entries', 'stream=nb_read_frames', '-of', 'csv=p=0',
                                    str(output / 'episode.mp4')], capture_output=True, text=True, check=True)
            self.assertEqual(probe.stdout.strip(), '2')


if __name__ == '__main__':
    unittest.main()
