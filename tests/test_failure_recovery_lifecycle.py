"""Regressions for navigation100 tasks 043 (SSH query) and 048 (deleted prim)."""
import importlib.util
from contextlib import nullcontext
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from manipulation_agent.contracts import SkillError
from manipulation_agent.executors.carry import ControlledCarry
from manipulation_agent.executors.placement import placement_transaction
from manipulation_agent.executors.omnigibson_rgb import RGBBackend
from manipulation_agent.executors.omnigibson_backend import OmniGibsonBackend

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
        b._anchored_operation = lambda target: nullcontext()
        b._placement_record = Mock()
        b._carry_follow = Mock()
        b.frames_revision = 2
        return b, held, target, scene, sim



    def test_runtime_failure_after_topology_change_preserves_new_scene(self):
        b, held, target, scene, sim = self.setup_backend()
        with self.assertRaises(SkillError) as failure:
            with placement_transaction(sim, b._placement_record):
                scene.objects = [target]
                raise KeyError('removed contact body')
        self.assertEqual(failure.exception.code, 'target_changed')
        sim.load_state.assert_not_called()


    def test_transition_error_keeps_the_executed_physics_step_in_video(self):
        b=RGBBackend.__new__(RGBBackend)
        b.deadline=Mock();b._base_target=None;b.steps=0
        b.og=SimpleNamespace(sim=SimpleNamespace(_render_on_step=True,render_on_step=lambda _:nullcontext()))
        b._check_renderer_health=Mock()
        b._restore_object_anchor=Mock();b._restore_stabilized_containers=Mock();b._carry_follow=Mock()
        b._check_placement_objects=Mock(side_effect=SkillError('target_changed','object replaced',changed=True))
        recorded=[]
        b._video_frame=lambda kind:recorded.append((kind,b.steps))
        def physics_step(*_):b.steps+=1
        with patch.object(OmniGibsonBackend,'_step',side_effect=physics_step), \
             patch('manipulation_agent.executors.omnigibson_rgb.component',return_value=nullcontext()):
            with self.assertRaises(SkillError):b._step('action')
            self.assertEqual(recorded,[('env_step',1)])
            b._check_placement_objects.side_effect=None
            b._step('next action')
            self.assertEqual(recorded,[('env_step',1),('env_step',2)])


if __name__ == '__main__':
    unittest.main()
