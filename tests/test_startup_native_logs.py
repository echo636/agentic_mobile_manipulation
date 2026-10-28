import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from manipulation_agent.batch.startup_logs import read_startup_logs, startup_reader_code
from manipulation_agent.batch.startup_progress import STARTUP_POLICY, startup_progress_update


class NativeLoadingProgressTests(unittest.TestCase):
    def test_native_loading_renews_but_repeated_resources_and_warnings_do_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'native_runtime.log'
            material = " [1ms] [Info] [omni.hydra] Setting new rendering material for prim '/material1': shader\n"
            p.write_text('2026-01-01T00:00:10Z' + material
                         + "2026-01-01T00:00:20Z [2ms] [Info] [omni.usd] OmniUsdResolver: Opening filesystem asset 'file:/texture.png' for read\n"
                         + '2026-01-01T00:00:30Z' + material
                         + '2026-01-01T00:00:40Z [Warning] [omni.hydra] retrying\n')
            began = 1767225600.
            row = dict(startup_watchdog_policy=STARTUP_POLICY, simulator_started_at_unix=began,
                       startup_deadline_unix=began+25, startup_timeout_seconds=25)
            result = read_startup_logs(tmp)
            row.update(startup_progress_update(row, result['events'], began+40))
            self.assertEqual(row['startup_deadline_unix'], began+45)
            self.assertEqual(startup_progress_update(row, result['events'], began+42), {})

    def test_mapped_zero_suffix_and_partial_line_are_read_after_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'native_runtime.log'
            first = b"2026-01-01T00:00:10Z [Info] [omni.hydra] Setting new rendering material for prim '/one': shader\n"
            second = first.replace(b'/one', b'/two')
            p.write_bytes(first + second[:20] + b'\0'*1000)
            result = read_startup_logs(tmp)
            self.assertEqual(result['native_offset'], len(first))
            with p.open('r+b') as stream:
                stream.seek(len(first)); stream.write(second)
            result = read_startup_logs(tmp, native_offset=result['native_offset'],
                                       native_identity=result['native_identity'])
            self.assertEqual(len(result['events']), 1)
            self.assertEqual(result['native_offset'], len(first+second))
            # Execute the actual self-contained worker command with system Python.
            remote = subprocess.run([sys.executable, '-c', startup_reader_code(tmp, {})],
                                    check=True, capture_output=True, text=True)
            self.assertEqual(len(json.loads(remote.stdout)['events']), 2)

    def test_internal_mapped_hole_does_not_hide_later_loading(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'native_runtime.log'
            line = b"2026-01-01T00:00:10Z [Info] [omni.hydra] Setting new rendering material for prim '/one': shader\n"
            p.write_bytes(line + b'\0'*1024 + line.replace(b'/one', b'/two') + b'\0'*1024)
            result = read_startup_logs(tmp)
            self.assertEqual(len(result['events']), 2)
            self.assertEqual(result['native_offset'], len(line))


if __name__ == '__main__':
    unittest.main()
