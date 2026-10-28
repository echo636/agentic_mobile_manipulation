"""A failed read-only handshake must not force an expensive episode reload."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

from manipulation_agent.runtime.deadline import EpisodeDeadline


spec = importlib.util.spec_from_file_location('preflight_controller',
    Path(__file__).parents[1] / 'scripts/run_codex_controller.py')
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)

SERVER = '''import json,sys
from pathlib import Path
p=Path(sys.argv[1]);n=int(p.read_text())+1 if p.exists() else 1;p.write_text(str(n))
for line in sys.stdin:
 m=json.loads(line)
 with p.with_suffix('.calls').open('a') as f:f.write(m['method']+'\\n')
 if n==1 and sys.argv[2]=='timeout':continue
 if 'id' not in m:continue
 result={'protocolVersion':'2024-11-05'} if m['method']=='initialize' else {'tools':[]}
 print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':result}),flush=True)
'''


class PreflightRecovery(unittest.TestCase):
    def test_timeout_reconnects_without_calls_and_preserves_both_attempts(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder); counter = output / 'starts'
            result = controller.preflight_server(sys.executable,
                ['-u', '-c', SERVER, str(counter), 'timeout'], [], output,
                EpisodeDeadline(), attempt_timeout=.5)
            self.assertEqual(result['status'], 'passed')
            self.assertEqual(counter.read_text(), '2')
            first = json.loads((output/'mcp_preflight_attempts/1/mcp_preflight.json').read_text())
            self.assertEqual((first['status'], first['phase']), ('failed', 'initialize'))
            self.assertEqual(first['error_type'], 'TimeoutError')
            self.assertEqual(json.loads((output/'mcp_preflight.json').read_text())['status'], 'passed')
            self.assertEqual(counter.with_suffix('.calls').read_text().splitlines(),
                ['initialize', 'initialize', 'notifications/initialized', 'tools/list'])

    def test_schema_mismatch_is_not_retried(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder); counter = output / 'starts'
            with self.assertRaisesRegex(RuntimeError, 'schemas differ'):
                controller.preflight_server(sys.executable,
                    ['-u', '-c', SERVER, str(counter), 'ready'],
                    [{'name': 'initialize', 'inputSchema': {}}], output,
                    EpisodeDeadline(), attempt_timeout=2)
            self.assertEqual(counter.read_text(), '1')

    def test_expired_legacy_deadline_does_not_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            with self.assertRaises(TimeoutError):
                controller.preflight_server(sys.executable,
                    ['-u', '-c', SERVER, str(output/'starts'), 'timeout'], [], output,
                    EpisodeDeadline(1), attempt_timeout=.5)
            self.assertFalse((output/'mcp_preflight_attempts/2').exists())


if __name__ == '__main__':
    unittest.main()
