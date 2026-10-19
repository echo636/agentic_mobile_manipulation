"""Reference recovery must retain failures and the common action ceiling."""
from pathlib import Path
from contextlib import redirect_stdout
import io
import tempfile
from types import SimpleNamespace
import unittest

from manipulation_agent.contracts import SkillError
from manipulation_agent.reference_tasks import ReferenceTask


class ReferenceRecoveryTests(unittest.TestCase):
    def reference(self, directory, failures, *, max_attempts=200):
        self.enterContext(redirect_stdout(io.StringIO()))
        backend = SimpleNamespace(steps=0, _ground=lambda: None, _predicate_values=lambda: {})

        def execute(primitive, target, max_steps, **kwargs):
            backend.steps += 1
            if primitive == 'open' and failures:
                raise failures.pop(0)
            return {'primitive': primitive}

        backend.execute_visual = execute
        reference = ReferenceTask(backend, Path(directory), max_attempts=max_attempts)
        # Coordinate selection is irrelevant to the retry; exercise the real
        # action counter, error ledger and result logging for every attempt.
        reference.move = lambda primitive, role: reference.action(primitive)
        return reference

    def displaced(self):
        return SkillError('postcondition_error', 'Operation displaced existing contents from the container')

    def test_recovered_open_preserves_failed_attempt_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            reference = self.reference(directory, [self.displaced(), self.displaced()])
            result = reference.open_delivered_kit()
            self.assertEqual(result['primitive'], 'open')
            self.assertEqual(sum(r['status']=='failed' for r in reference.calls), 2)
            self.assertEqual(len(reference.calls), 6)

    def test_recovery_cannot_exceed_the_remaining_action_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            reference = self.reference(directory, [self.displaced()], max_attempts=3)
            with self.assertRaisesRegex(RuntimeError, 'budget exhausted'):
                reference.open_delivered_kit()
            self.assertEqual(len(reference.calls), 3)

    def test_unrelated_open_error_is_not_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            reference = self.reference(directory, [SkillError('out_of_reach', 'Move closer')])
            with self.assertRaises(SkillError) as caught:
                reference.open_delivered_kit()
            self.assertEqual(caught.exception.code, 'out_of_reach')
            self.assertEqual(sum(r['primitive']=='open' for r in reference.calls), 1)

    def test_persistent_containment_failure_never_counts_as_success(self):
        with tempfile.TemporaryDirectory() as directory:
            reference = self.reference(directory, [self.displaced() for _ in range(3)])
            with self.assertRaises(SkillError):
                reference.open_delivered_kit()
            self.assertEqual(sum(r['status']=='failed' for r in reference.calls), 3)


if __name__ == '__main__':
    unittest.main()
