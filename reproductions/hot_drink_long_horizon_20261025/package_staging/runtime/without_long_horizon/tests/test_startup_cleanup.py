import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from manipulation_agent.deadline import EpisodeDeadline
from manipulation_agent.startup_cleanup import shutdown_partial_simulator
from manipulation_agent import vision_cli


class OutOfMemoryError(MemoryError):
    pass


class PartialStartupCleanupTests(unittest.TestCase):
    def run_failed_constructor(self, shutdown):
        error = OutOfMemoryError('primary CUDA allocation failure')
        og = SimpleNamespace(app=object(), shutdown=shutdown)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'run'
            argv = ['manip-agent', '--backend', 'omnigibson', '--output', str(output),
                    '--instruction', 'CPU failure injection']
            with patch.object(sys, 'argv', argv), patch.dict(sys.modules, {'omnigibson': og}), \
                    patch.object(vision_cli.EpisodeDeadline, 'from_env', return_value=EpisodeDeadline()), \
                    patch.object(vision_cli, 'RGBBackend', side_effect=error), \
                    patch.object(vision_cli.faulthandler, 'enable'), \
                    patch.object(vision_cli.faulthandler, 'register'):
                with self.assertRaises(OutOfMemoryError) as caught:
                    vision_cli.main()
            self.assertIs(caught.exception, error)
            run = json.loads((output / 'run.json').read_text())
            self.assertEqual(run['status'], 'failed')
            self.assertIsNone(run['task_success'])
            self.assertEqual(run['failure'], 'OutOfMemoryError')
            self.assertEqual(run['scoring']['status'], 'not_attempted')
            self.assertIn('primary CUDA allocation failure', (output / 'traceback.txt').read_text())
            events = [json.loads(line) for line in (output / 'events.jsonl').read_text().splitlines()]
            self.assertEqual(events[0]['kind'], 'runtime_failure')
            self.assertEqual(events[0]['type'], 'OutOfMemoryError')
            self.assertEqual(events[-1]['kind'], 'startup_cleanup')
            shutdown.assert_called_once_with()
            return events[-1]

    def test_constructor_failure_closes_existing_app_and_preserves_primary_record(self):
        event = self.run_failed_constructor(Mock())
        self.assertEqual(event['status'], 'passed')  # Cleanup only, never task success.

    def test_secondary_cleanup_exit_does_not_replace_original_constructor_error(self):
        event = self.run_failed_constructor(Mock(side_effect=SystemExit(0)))
        self.assertEqual(event['status'], 'failed')
        self.assertEqual(event['error_type'], 'SystemExit')

    def test_absent_or_uninitialized_app_never_imports_or_calls_shutdown(self):
        for og in (None, SimpleNamespace(app=None, shutdown=Mock())):
            with self.subTest(og=og), patch.dict(sys.modules, {'omnigibson': og}):
                result = shutdown_partial_simulator()
                self.assertEqual(result['status'], 'skipped')
                self.assertIs(sys.modules['omnigibson'], og)
                if og is not None:
                    og.shutdown.assert_not_called()

    def test_initialized_backend_keeps_its_single_normal_close(self):
        backend = SimpleNamespace(close=Mock())
        og = SimpleNamespace(app=object(), shutdown=Mock())
        def harness_factory(backend, recorder, budget, **kwargs):
            return SimpleNamespace(recorder=recorder, finalize_recording=Mock())
        def finish(harness, port):
            harness.recorder.finish({'status': 'passed', 'task_success': True}, render=False)
        with tempfile.TemporaryDirectory() as directory:
            argv = ['manip-agent', '--backend', 'omnigibson', '--output', str(Path(directory) / 'run'),
                    '--instruction', 'CPU close lifecycle fixture']
            with patch.object(sys, 'argv', argv), patch.dict(sys.modules, {'omnigibson': og}), \
                    patch.object(vision_cli.EpisodeDeadline, 'from_env', return_value=EpisodeDeadline()), \
                    patch.object(vision_cli, 'RGBBackend', return_value=backend), \
                    patch.object(vision_cli, 'VisionHarness', side_effect=harness_factory), \
                    patch.object(vision_cli.bridge, 'serve', side_effect=finish), \
                    patch.object(vision_cli.faulthandler, 'enable'), \
                    patch.object(vision_cli.faulthandler, 'register'):
                self.assertEqual(vision_cli.main(), 0)
        backend.close.assert_called_once_with()
        og.shutdown.assert_not_called()


if __name__ == '__main__':
    unittest.main()
