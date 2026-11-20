"""GPU memory opt-out must bypass capacity only, preserving real failures."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch


SOURCE = Path(__file__).resolve().parents[1] / 'scripts/behavior100_remote.py'
SPEC = importlib.util.spec_from_file_location('gpu_policy_admission', SOURCE)
remote = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(remote)


class GPUMemoryPolicyTests(unittest.TestCase):
    def admission(self, skip, driver='580.173.02'):
        def content(path, *args, **kwargs):
            if str(path) == '/proc/meminfo':
                return 'MemAvailable: 100000000 kB\n'
            return {'memory.current':'1024', 'memory.max':'max',
                    'memory.stat':'file 0\nshmem 0\n'}[path.name]

        def command(args):
            # A busy GPU has only 564 MiB free, well below the old gate.
            output = f'0, GPU-fixture, 24000, 24564, {driver}\n' if args[0]=='nvidia-smi' else ''
            return {'exit_code':0,'stdout':output,'stderr':''}

        sock = MagicMock()
        sock.__enter__.return_value.connect_ex.return_value = 1
        with patch.object(Path, 'read_text', content), \
             patch.object(remote, 'command', side_effect=command), \
             patch.object(remote, 'local_quota', return_value={'stdout':''}), \
             patch.object(remote.shutil, 'disk_usage', return_value=SimpleNamespace(free=100*1024**3)), \
             patch.object(remote, 'runtime_storage_readiness', return_value={'status':'passed'}), \
             patch.object(remote.socket, 'socket', return_value=sock):
            return remote.preflight(0,31310,Path('/tmp'),light=True,
                min_free_gpu_mib=0,skip_gpu_memory_admission=skip)

    def test_explicit_opt_out_admits_busy_gpu(self):
        result = self.admission(True)
        self.assertNotIn('gpu_capacity', result['checks'])
        self.assertEqual(result['status'], 'passed')
        self.assertFalse(result['gpu_policy']['memory_admission_enforced'])

    def test_historical_default_retains_capacity_check(self):
        result = self.admission(False)
        self.assertFalse(result['checks']['gpu_capacity'])
        self.assertEqual(result['status'], 'blocked')

    def test_memory_opt_out_does_not_hide_driver_failure(self):
        result = self.admission(True, driver='570.1.0')
        self.assertFalse(result['checks']['validated_driver_floor'])
        self.assertEqual(result['status'], 'blocked')
