import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from manipulation_agent.references.reference_evidence import complete_reference_exit


class ReferenceExitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)

    def native_close(self, status='passed', exit_code=0):
        code = '''import os, sys
from pathlib import Path
from manipulation_agent.references.reference_evidence import close_reference
class Backend:
    def close(self): os._exit(int(sys.argv[3]))
close_reference(Backend(), {'status':sys.argv[2], 'evaluation':{'task_success':sys.argv[2]=='passed'},
                          'action_attempts':14}, Path(sys.argv[1]))
'''
        child = subprocess.run([sys.executable, '-c', code, str(self.output), status, str(exit_code)],
                               env={**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1]/'src')})
        return child.returncode

    def test_native_exit_preserves_score_before_cleanup_and_requires_parent(self):
        code = self.native_close()
        before = json.loads((self.output/'validation.json').read_text())
        self.assertEqual(before['cleanup_status'], 'running')
        self.assertTrue(complete_reference_exit(self.output, code))
        after = json.loads((self.output/'validation.json').read_text())
        self.assertEqual(after['cleanup_status'], 'passed')
        self.assertEqual(after['evaluation'], before['evaluation'])

    def test_zero_native_exit_does_not_turn_task_failure_into_success(self):
        code = self.native_close('failed')
        self.assertFalse(complete_reference_exit(self.output, code))
        record = json.loads((self.output/'validation.json').read_text())
        self.assertEqual(record['status'], 'failed')
        self.assertEqual(record['cleanup_status'], 'passed')
        self.assertFalse(record['evaluation']['task_success'])

    def test_crash_after_score_is_not_accepted(self):
        code = self.native_close(exit_code=139)
        self.assertFalse(complete_reference_exit(self.output, code))
        self.assertEqual(json.loads((self.output/'validation.json').read_text())['cleanup_status'], 'failed')

    def test_missing_evaluation_does_not_invent_score(self):
        self.assertFalse(complete_reference_exit(self.output, 0))
        self.assertFalse((self.output/'validation.json').exists())
        self.assertEqual(json.loads((self.output/'process_exit.json').read_text())['status'], 'failed')

    def test_existing_output_is_rejected_without_rewriting_evidence(self):
        marker = self.output/'validation.json'
        marker.write_text('{"status":"passed"}')
        script = Path(__file__).resolve().parents[1]/'scripts/validate_custom_task_physics.py'
        result = subprocess.run([sys.executable, str(script), '--spec', 'unused.json',
                                 '--order', 'cleanup_first', '--output', str(self.output)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('already exists', result.stderr)
        self.assertEqual(marker.read_text(), '{"status":"passed"}')


if __name__ == '__main__':
    unittest.main()
