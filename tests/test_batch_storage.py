import errno
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from manipulation_agent import batch_storage as storage

spec = importlib.util.spec_from_file_location('io_batch', Path(__file__).resolve().parents[1] / 'scripts/run_behavior100.py')
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)


class BatchStorageTests(unittest.TestCase):
    def make_store(self, folder):
        root = Path(folder) / 'shared'; root.mkdir(exist_ok=True)
        reports = root / 'reports'
        manifest = {'tasks': [{'run_id': 'episode', 'task': 'books', 'index': 23, 'status': 'planned'}]}
        state = storage.BatchStorage(root, reports, manifest, spool=Path(folder) / 'local',
                                     retry_seconds=.02, renderer=lambda progress: '<html/>')
        return state

    def submit(self, state, *, finished=False):
        row = {'run_id': 'episode', 'task': 'books', 'index': 23,
               'status': 'failed' if finished else 'running', 'task_success': False if finished else None,
               'updated_at': storage.now()}
        state.submit({'updated_at': storage.now(), 'tasks': [row]}, {'status': row['status']}, '<html>record</html>')
        return row

    def test_each_shared_write_failure_is_isolated_and_latest_record_is_retried(self):
        paths = ['records/episode.json', 'progress.json', 'run_records.json', 'validation.json',
                 'reports/behavior100/progress.json', 'reports/behavior100/validation.json',
                 'reports/behavior100.html']
        original = storage.atomic_text
        for relative in paths:
            with self.subTest(path=relative), tempfile.TemporaryDirectory() as folder:
                state = self.make_store(folder)
                failing = threading.Event(); failing.set()
                observed = threading.Event()
                def write(path, text, **kwargs):
                    if path == state.root / relative and failing.is_set():
                        observed.set()
                        raise OSError(errno.EIO, 'injected shared storage failure')
                    return original(path, text, **kwargs)
                try:
                    with patch.object(storage, 'atomic_text', side_effect=write):
                        self.submit(state)
                        self.assertTrue(observed.wait(2))
                        final = self.submit(state, finished=True)
                        self.assertEqual(state.recovered_rows()['episode'], final)
                        self.assertFalse(state.flush(.03))
                        self.assertEqual(state.status()['last_error']['errno'], errno.EIO)
                        failing.clear()
                        self.assertTrue(state.flush(3))
                        self.assertEqual(json.loads((state.root / 'records/episode.json').read_text()), final)
                        self.assertFalse(json.loads((state.root / 'progress.json').read_text())['tasks'][0]['task_success'])
                finally:
                    state.close(1)

    def test_real_controller_process_completes_despite_heartbeat_publication_eio(self):
        with tempfile.TemporaryDirectory() as folder:
            state = self.make_store(folder)
            b = batch.Batch.__new__(batch.Batch)
            b.storage = state; b.lock = threading.RLock(); b.root = state.root
            b.rows = [{'run_id': 'episode', 'status': 'running', 'execution_started_at_unix': time.time()}]
            b.source_info = {}; b.manifest = {}; b.workers = []; b.worker_states = {}
            b.controller_metadata = Mock(return_value={'status': 'passed'})
            b.refresh_startup_progress = Mock(); b.step_episode_closed = Mock(return_value=False)
            b.stop_controller = Mock(); b.finish_and_wait = Mock()
            error_seen = threading.Event()
            def fail(*args):
                error_seen.set()
                raise OSError(errno.EIO, 'injected NAS error')
            sleep = time.sleep
            process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(.25)'])
            try:
                with patch.object(state, '_publish', side_effect=fail), \
                        patch.object(batch, 'render_dashboard', return_value='<html/>'), \
                        patch.object(batch.time, 'sleep', side_effect=lambda seconds: sleep(min(seconds, .01))):
                    result = b.wait_controller(b.rows[0], process, Path(folder))
                    self.assertTrue(error_seen.wait(1))
                    self.assertEqual(process.returncode, 0)
                    self.assertEqual(result['status'], 'passed')
                    self.assertIn('heartbeat_at', state.recovered_rows()['episode'])
                    self.assertNotIn('failure', b.rows[0])
                    b.stop_controller.assert_not_called()
                    b.finish_and_wait.assert_not_called()
                self.assertTrue(state.flush(3))
            finally:
                if process.poll() is None:
                    process.kill(); process.wait()
                state.close(1)

    def test_slow_shared_filesystem_never_holds_update_or_shutdown(self):
        with tempfile.TemporaryDirectory() as folder:
            state = self.make_store(folder)
            entered = threading.Event(); release = threading.Event()
            original = state._publish
            def slow(*args):
                entered.set(); release.wait(5); original(*args)
            try:
                with patch.object(state, '_publish', side_effect=slow):
                    self.submit(state)
                    self.assertTrue(entered.wait(1))
                    start = time.monotonic()
                    final = self.submit(state, finished=True)
                    self.assertLess(time.monotonic() - start, .5)
                    self.assertEqual(state.recovered_rows()['episode'], final)
                    self.assertFalse(state.flush(.02))
                    release.set()
                    self.assertTrue(state.flush(3))
            finally:
                release.set(); state.close(1)

    def test_pending_checkpoint_survives_supervisor_restart_without_restarting_episode(self):
        with tempfile.TemporaryDirectory() as folder:
            state = self.make_store(folder)
            with patch.object(state, '_publish', side_effect=OSError(errno.EIO, 'NAS offline')):
                final = self.submit(state, finished=True)
                self.assertFalse(state.close(.03))
            restarted = self.make_store(folder)
            try:
                self.assertEqual(restarted.recovered_rows()['episode'], final)
                self.assertTrue(restarted.flush(3))
                self.assertEqual(json.loads((state.root / 'records/episode.json').read_text()), final)
            finally:
                restarted.close(1)

    def test_journal_retry_preserves_external_audit_and_deduplicates_event(self):
        with tempfile.TemporaryDirectory() as folder:
            state = self.make_store(folder)
            journal = state.root / 'journal.md'
            journal.write_text('External audit entry\n')
            original = state._write
            def fail_after_append(path, text):
                if path.name == 'journal.md':
                    raise OSError(errno.EIO, 'public journal offline')
                original(path, text)
            try:
                with patch.object(state, '_write', side_effect=fail_after_append):
                    state.journal('START episode')
                    self.assertFalse(state.flush(.05))
                with journal.open('a') as stream:
                    stream.write('Another external audit entry\n')
                self.assertTrue(state.flush(3))
                text = journal.read_text()
                self.assertEqual(text.count('START episode'), 1)
                self.assertIn('External audit entry', text)
                self.assertIn('Another external audit entry', text)
            finally:
                state.close(1)

    def test_local_disk_error_keeps_active_state_and_retries_without_next_heartbeat(self):
        with tempfile.TemporaryDirectory() as folder:
            state = self.make_store(folder)
            try:
                with patch.object(state, '_checkpoint', side_effect=OSError(errno.ENOSPC, 'local disk full')):
                    final = self.submit(state, finished=True)
                    self.assertIsNotNone(state.status()['local_checkpoint_error'])
                    self.assertEqual(state.recovered_rows()['episode'], final)
                    self.assertFalse(state.flush(.05))
                self.assertTrue(state.flush(3))
                checkpoint = json.loads((state.spool / 'checkpoint.json').read_text())
                self.assertEqual(checkpoint['snapshot']['progress']['tasks'][0], final)
            finally:
                state.close(1)

    def test_checkpoint_identity_mismatch_is_not_silently_reused(self):
        with tempfile.TemporaryDirectory() as folder:
            state = self.make_store(folder)
            self.submit(state); state.close(3)
            with self.assertRaisesRegex(ValueError, 'another batch/manifest'):
                storage.BatchStorage(state.root, state.reports, {'tasks': []}, spool=state.spool)

    def test_dashboard_template_read_error_is_retried_off_control_thread(self):
        with tempfile.TemporaryDirectory() as folder:
            state = self.make_store(folder)
            state.renderer = Mock(side_effect=OSError(errno.EIO, 'template read failed'))
            try:
                state.submit({'updated_at': storage.now(), 'tasks': []}, {'status': 'running'})
                self.assertFalse(state.flush(.05))
                self.assertEqual(state.status()['last_error']['errno'], errno.EIO)
                state.renderer = Mock(return_value='<html>recovered</html>')
                self.assertTrue(state.flush(3))
                self.assertIn('recovered', (state.reports / 'behavior100.html').read_text())
            finally:
                state.close(1)

    def test_batch_restart_uses_newer_local_final_record_without_requeue(self):
        with tempfile.TemporaryDirectory() as folder:
            state = self.make_store(folder)
            (state.root / 'manifest.json').write_text(json.dumps(state.manifest))
            with patch.object(state, '_publish', side_effect=OSError(errno.EIO, 'NAS unavailable')):
                final = self.submit(state, finished=True)
                self.assertFalse(state.close(.03))
            records = state.root / 'records'; records.mkdir(exist_ok=True)
            (records / 'episode.json').write_text(json.dumps({**final, 'status': 'planned', 'updated_at': ''}))
            config = {'batch': str(state.root), 'reports': str(state.reports),
                      'supervisor_spool_dir': str(state.spool), 'workers': [
                          {'id': 'fixture', 'gpu': 0, 'ssh': ['ssh', 'host'],
                           'base_port': 36000, 'sim_python': sys.executable}]}
            with patch.object(batch, 'source_version', return_value={'commit': 'fixture', 'dirty': False}), \
                    patch.object(batch.shutil, 'which', return_value='/fixture/codex'), \
                    patch.object(batch.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)):
                restarted = batch.Batch(config)
            try:
                self.assertEqual(restarted.rows[0], final)
                self.assertNotIn('adopt_inflight', config)
            finally:
                restarted.storage.close(1)


if __name__ == '__main__':
    unittest.main()
