"""Joint cleanup ordering at the native physics callback boundary."""
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from manipulation_agent.executors.compat.attachment_break import install_attachment_break_deferral


class AttachmentBreakTests(unittest.TestCase):
    def test_break_cleanup_waits_for_native_return_and_retains_order(self):
        calls = []
        sim = SimpleNamespace(currently_stepping=True)
        sim._report_step_exceptions = lambda: calls.append('report')

        class AttachedTo:
            def on_joint_break(self, path):
                if sim.currently_stepping:
                    raise RuntimeError('PhysX callback lock is held')
                calls.append(path)

        with patch.dict(sys.modules, {'omnigibson': SimpleNamespace(sim=sim),
                                     'omnigibson.object_states': SimpleNamespace(AttachedTo=AttachedTo)}):
            install_attachment_break_deferral()
            install_attachment_break_deferral()
            state = AttachedTo()
            state.on_joint_break('joint1')
            state.on_joint_break('joint2')
            self.assertEqual(calls, [])
            sim.currently_stepping = False
            sim._report_step_exceptions()
            self.assertEqual(calls, ['report', 'joint1', 'joint2'])
            sim._report_step_exceptions()
            self.assertEqual(calls, ['report', 'joint1', 'joint2', 'report'])
            state.on_joint_break('outside_callback')
            self.assertEqual(calls[-1], 'outside_callback')

    def test_native_cleanup_errors_are_not_swallowed_or_replayed(self):
        sim = SimpleNamespace(currently_stepping=True, _report_step_exceptions=lambda: None)
        calls = []

        class AttachedTo:
            def on_joint_break(self, path):
                calls.append(path)
                raise ValueError('native cleanup failed')

        with patch.dict(sys.modules, {'omnigibson': SimpleNamespace(sim=sim),
                                     'omnigibson.object_states': SimpleNamespace(AttachedTo=AttachedTo)}):
            install_attachment_break_deferral()
            AttachedTo().on_joint_break('joint')
            sim.currently_stepping = False
            with self.assertRaisesRegex(ValueError, 'native cleanup failed'):
                sim._report_step_exceptions()
            sim._report_step_exceptions()
            self.assertEqual(calls, ['joint'])
