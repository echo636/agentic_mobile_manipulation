"""Physics results must survive an executor step, including failed actions."""
from contextlib import nullcontext
from types import SimpleNamespace as NS
from unittest import TestCase
from unittest.mock import Mock, patch

from manipulation_agent.executors.omnigibson_rgb import RGBBackend
from manipulation_agent.omnigibson_backend import OmniGibsonBackend


class PhysicsStateOwnershipTests(TestCase):
    def backend(self):
        backend = RGBBackend.__new__(RGBBackend)
        backend._check_renderer_health = Mock()
        backend.deadline = NS(check=Mock())
        backend._base_target = None
        backend.og = NS(sim=NS(_render_on_step=True, render_on_step=lambda _: nullcontext()))
        backend._check_placement_objects = Mock()
        backend._video_frame = Mock()
        backend._transfer_base_pose = Mock()
        # Legacy state may exist in an adopted fixture; stepping must ignore it.
        backend._restore_base_target = Mock(side_effect=AssertionError('Rewrote physics result'))
        backend._carry_follow = Mock(side_effect=AssertionError('Projected grasp pose'))
        backend._restore_stabilized_containers = Mock(side_effect=AssertionError('Pinned released body'))
        return backend

    def test_idle_step_keeps_physics_displacement_without_pose_writes(self):
        backend = self.backend()
        body = NS(height=1.)
        def physics(_):
            body.height -= .1
        with patch.object(OmniGibsonBackend, '_step', side_effect=physics) as step:
            backend._step('hold')
            backend._step('hold')
        self.assertAlmostEqual(body.height, .8)
        self.assertEqual(step.call_count, 2)
        backend._transfer_base_pose.assert_not_called()

    def test_base_command_is_consumed_once_and_not_reapplied_after_collision(self):
        backend = self.backend()
        body = NS(x=0.)
        backend._base_target = {'position': 2., 'orientation': 0.}
        backend._transfer_base_pose.side_effect = lambda x, _: setattr(body, 'x', x)
        def physics(_):
            body.x += .1
        with patch.object(OmniGibsonBackend, '_step', side_effect=physics):
            backend._step('hold')
            backend._step('hold')
        self.assertAlmostEqual(body.x, 2.2)
        backend._transfer_base_pose.assert_called_once_with(2., 0.)
        self.assertIsNone(backend._base_target)

    def test_native_failure_restores_render_setting_not_world_state(self):
        backend = self.backend()
        with patch.object(OmniGibsonBackend, '_step', side_effect=RuntimeError('Native fault')):
            with self.assertRaisesRegex(RuntimeError, 'Native fault'):
                backend._step('hold')
        self.assertTrue(backend.og.sim._render_on_step)
        backend._transfer_base_pose.assert_not_called()
