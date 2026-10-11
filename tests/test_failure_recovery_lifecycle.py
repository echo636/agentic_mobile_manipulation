"""Regressions for navigation100 tasks 043 (SSH query) and 048 (deleted prim)."""
import importlib.util
from contextlib import nullcontext
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from manipulation_agent.contracts import SkillError
from manipulation_agent.executors.carry import ControlledCarry
from manipulation_agent.executors.placement import placement_transaction

spec = importlib.util.spec_from_file_location('recovery_batch', Path(__file__).resolve().parents[1]/'scripts/run_behavior100.py')
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)


class StartupQueryRecovery(unittest.TestCase):
    def test_lost_query_does_not_kill_live_simulator_and_next_query_recovers(self):
        b = batch.Batch.__new__(batch.Batch)
        b.journal = Mock()
        b.ssh = Mock(side_effect=[subprocess.CompletedProcess([], 255, '', 'ssh failure'),
                                 subprocess.CompletedProcess([], 0, '', ''),
                                 subprocess.TimeoutExpired('ssh', 40),
                                 subprocess.CompletedProcess([], 0, 'ActiveState=active\nMainPID=123\n', '')])
        for _ in range(3):
            self.assertIsNone(b.initializing_unit_state('owned.service'))
        self.assertEqual(b.initializing_unit_state('owned.service')['MainPID'], '123')
        self.assertEqual(b.journal.call_count, 3)

    def test_confirmed_exit_is_still_reported(self):
        b = batch.Batch.__new__(batch.Batch)
        b.unit_state = Mock(return_value={'ActiveState':'failed', 'MainPID':'0', 'ExecMainStatus':'1'})
        with self.assertRaisesRegex(RuntimeError, 'Simulator exited'):
            b.initializing_unit_state('owned.service')


class DeletedPlacementObject(unittest.TestCase):
    def setup_backend(self):
        held, target = object(), object()
        scene = SimpleNamespace(objects=[held, target])
        sim = SimpleNamespace(scenes=[scene], dump_state=Mock(return_value={}), load_state=Mock())
        b = ControlledCarry()
        b.env = SimpleNamespace(scene=scene)
        b.og = SimpleNamespace(sim=sim)
        b.ideal_carry = True
        b._ideal_held, b._carry_relative = held, 'pose'
        b._carry_contents, b._carry_dependencies = [], []
        b._anchored_operation = lambda target: nullcontext()
        b._placement_record = Mock()
        b._carry_follow = Mock()
        b.frames_revision = 2
        return b, held, target, scene, sim

    def test_replaced_held_object_is_not_rolled_back_or_reattached(self):
        b, held, target, scene, sim = self.setup_backend()
        with self.assertRaises(SkillError) as failure:
            with b._placement_context(watch=(held, target)):
                b._carry_detach()
                scene.objects = [target, object(), object()]
                b._check_placement_objects()
        self.assertEqual(failure.exception.code, 'target_changed')
        self.assertTrue(failure.exception.changed)
        sim.load_state.assert_not_called()
        b._carry_follow.assert_not_called()
        self.assertIsNone(b._ideal_held)
        self.assertEqual(b.frames_revision, -1)
        self.assertEqual(b._placement_watch, ())
        self.assertEqual(b._placement_record.call_args.args[0]['status'], 'rollback_skipped')

    def test_ordinary_sampling_failure_still_restores_carry(self):
        b, held, target, scene, sim = self.setup_backend()
        with self.assertRaisesRegex(ValueError, 'sampling'):
            with b._placement_context(watch=(held, target)):
                b._carry_detach()
                raise ValueError('sampling failed')
        sim.load_state.assert_called_once()
        b._carry_follow.assert_called_once()
        self.assertIs(b._ideal_held, held)

    def test_runtime_failure_after_topology_change_preserves_new_scene(self):
        b, held, target, scene, sim = self.setup_backend()
        with self.assertRaises(SkillError) as failure:
            with placement_transaction(sim, b._placement_record):
                scene.objects = [target]
                raise KeyError('removed contact body')
        self.assertEqual(failure.exception.code, 'target_changed')
        sim.load_state.assert_not_called()

    def test_carry_follow_clears_deleted_root_before_accessing_prim(self):
        b, held, target, scene, sim = self.setup_backend()
        scene.objects = [target]
        with self.assertRaises(SkillError) as failure:
            ControlledCarry._carry_follow(b)
        self.assertEqual(failure.exception.code, 'target_changed')
        self.assertIsNone(b._ideal_held)


if __name__ == '__main__':
    unittest.main()
