import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / 'scripts' / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


remote = module('storage_remote', 'behavior100_remote.py')
batch = module('storage_batch', 'run_behavior100.py')


class RuntimeStorageTests(unittest.TestCase):
    def test_cache_symlink_uses_target_disk_and_blocks_low_space(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / 'local_cache'; cache.mkdir()
            alias = root / 'cache_alias'; alias.symlink_to(cache, target_is_directory=True)
            with patch.object(remote.shutil, 'disk_usage', return_value=SimpleNamespace(free=8 * 1024**3)) as usage:
                result = remote.runtime_storage_readiness([alias])
            usage.assert_called_once_with(cache.resolve())
            self.assertEqual(result['status'], 'blocked')
            self.assertEqual(result['paths'][0]['resolved_path'], str(cache.resolve()))

    def test_cache_and_tmp_are_both_checked_and_missing_path_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / 'cache'; cache.mkdir()
            scratch = Path(tmp) / 'scratch'
            with patch.object(remote.shutil, 'disk_usage', return_value=SimpleNamespace(free=64 * 1024**3)):
                result = remote.runtime_storage_readiness([cache, scratch])
            self.assertEqual([r['status'] for r in result['paths']], ['passed', 'blocked'])
            self.assertEqual(result['status'], 'blocked')

    def test_record_checks_scratch_writability_even_with_disk_space(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(remote.shutil, 'disk_usage', return_value=SimpleNamespace(free=64 * 1024**3)), \
                 patch.object(remote.os, 'access', return_value=False):
                self.assertEqual(remote.runtime_storage_readiness([tmp])['status'], 'blocked')

    def test_remote_arguments_keep_spaces_and_check_both_paths(self):
        config = {'simulator_appdata_path': '/local/cache with spaces', 'simulator_tmpdir': '/scratch/tmp'}
        self.assertEqual(batch.runtime_storage_args(config), [
            '--runtime-storage-path', '/local/cache with spaces',
            '--runtime-storage-path', '/scratch/tmp',
        ])
        self.assertEqual(batch.runtime_storage_args({}), [])


if __name__ == '__main__':
    unittest.main()
