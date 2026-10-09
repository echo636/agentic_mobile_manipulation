import importlib.util
from pathlib import Path
from types import SimpleNamespace
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('remote_readiness', Path(__file__).resolve().parents[1] / 'scripts/behavior100_remote.py')
remote = importlib.util.module_from_spec(spec)
spec.loader.exec_module(remote)


class ImportReadinessTests(unittest.TestCase):
    def test_numeric_quota_headroom_and_unlimited_filesystems(self):
        text='Filesystem blocks quota limit grace files quota limit grace\n/dev/root 10485760 20971520 20971520 10 0 0\n/nas 999999999 0 0 10 0 0\n'
        self.assertEqual(remote.quota_headroom(text),10*1024**3)
        self.assertEqual(remote.quota_headroom('/dev/root 20971521* 20971520 20971520 10 0 0'),0)
        self.assertIsNone(remote.quota_headroom('no quota configured'))

    def quota_preflight(self, quota_result):
        def query(args):
            if args[0] == 'quota':
                self.assertEqual(args, ['quota', '-w', '-v', '-l'])
                if isinstance(quota_result, BaseException):
                    raise quota_result
                return quota_result
            if args[0] == 'nvidia-smi':
                return {'exit_code': 0, 'stdout': '0, GPU-fixture, 0, 49152, 580.65.06', 'stderr': ''}
            return {'exit_code': 0, 'stdout': '', 'stderr': ''}
        def file_text(path):
            if str(path) == '/proc/meminfo':
                return 'MemAvailable: 104857600 kB\n'
            return {'memory.current': '0', 'memory.max': 'max',
                    'memory.stat': 'file 0\nshmem 0\n'}[path.name]
        with patch.object(remote, 'command', side_effect=query), \
                patch.object(Path, 'read_text', file_text), \
                patch.object(remote.shutil, 'disk_usage', return_value=SimpleNamespace(free=100*1024**3)), \
                patch.object(remote.socket, 'socket') as sock:
            sock.return_value.__enter__.return_value.connect_ex.return_value = 1
            return remote.preflight(0, 36000, '/fixture/data', light=True)

    def test_quota_timeout_is_unavailable_and_does_not_block_other_resource_checks(self):
        result = self.quota_preflight(subprocess.TimeoutExpired(['quota'], 30))
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['queries']['quota']['status'], 'unavailable')
        self.assertEqual(result['queries']['quota']['error_type'], 'TimeoutExpired')
        self.assertIsNone(result['quota_free_bytes'])
        self.assertTrue(result['checks']['data_disk_free_40GiB'])
        self.assertTrue(result['checks']['host_memory_available_40GiB'])

    def test_missing_quota_command_or_unconfigured_quota_does_not_block(self):
        for response in [FileNotFoundError('quota'),
                         {'exit_code': 0, 'stdout': 'no quota configured', 'stderr': ''}]:
            with self.subTest(response=type(response).__name__):
                result = self.quota_preflight(response)
                self.assertEqual(result['status'], 'passed')
                self.assertIsNone(result['quota_free_bytes'])
                self.assertNotIn('quota_headroom_2GiB', result['checks'])

    def test_exhausted_local_quota_still_blocks_including_partial_timeout_output(self):
        exhausted = '/dev/root 20971521* 20971520 20971520 10 0 0\n'
        for response in [{'exit_code': 1, 'stdout': exhausted, 'stderr': ''},
                         subprocess.TimeoutExpired(['quota'], 30, output=exhausted.encode())]:
            with self.subTest(response=type(response).__name__):
                result = self.quota_preflight(response)
                self.assertEqual(result['status'], 'blocked')
                self.assertEqual(result['quota_free_bytes'], 0)
                self.assertFalse(result['checks']['quota_headroom_2GiB'])

    def test_wrong_host_editable_and_missing_import_are_rejected_without_loading_simulator(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            paths = {name: root / 'src/BEHAVIOR-1K' / path for name, path in {
                'bddl': 'bddl3/bddl/__init__.py',
                'omnigibson': 'OmniGibson/omnigibson/__init__.py',
                'gello': 'joylo/gello/__init__.py'}.items()}
            for path in paths.values():
                path.parent.mkdir(parents=True); path.write_text('raise AssertionError("must not import")')
            specs = {name: SimpleNamespace(origin=str(path)) for name, path in paths.items()}
            with patch.object(remote.importlib.util, 'find_spec', side_effect=specs.get):
                self.assertEqual(remote.source_import_readiness(root)['status'], 'passed')
                specs['bddl'] = None
                self.assertEqual(remote.source_import_readiness(root)['packages']['bddl']['status'], 'failed')
                other = root / 'other-source.py'; other.write_text('')
                specs['bddl'] = SimpleNamespace(origin=str(other))
                self.assertEqual(remote.source_import_readiness(root)['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
