"""CPU adapter-boundary checks, not physical IK validation."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
import torch

from manipulation_agent.contracts import SkillError
from manipulation_agent.executors.oracle_official_ik import OfficialIKAdapter


class OfficialIKAdapterTests(unittest.TestCase):
    def setUp(self):
        self.created = []
        self.indices = torch.tensor([6, 7, 8])
        self.robot = SimpleNamespace(
            _default_arm_ik_controller_configs={
                arm: {"name": "InverseKinematicsController", "dof_idx": torch.tensor([7, 8]),
                      "mode": "pose_delta_ori", "smoothing_filter_size": 2}
                for arm in ("left", "right")},
            articulation_root_path="/World/robot",
            eef_link_names={"left": "left_eef", "right": "right_eef"},
        )
        self.adapter = object.__new__(OfficialIKAdapter)
        self.adapter.robot = self.robot
        self.adapter.controllers = {}
        self.adapter.calls = 0
        def factory(**config):
            c = Mock()
            c.compute_control.return_value = torch.tensor([[.1, .2, .3]])
            c.clip_control.side_effect = lambda output: output
            c.config = config
            self.created.append(c)
            return c
        self.adapter.controller_type = factory
        self.adapter.compute_backend = SimpleNamespace(
            from_torch=lambda value: value, to_torch=lambda value: value,
            T=SimpleNamespace(quat2mat=lambda q: torch.eye(3).unsqueeze(0)))
        self.position = torch.tensor([.4, .2, .8])
        self.quaternion = torch.tensor([0., 0., 0., 1.])

    def target(self):
        return self.adapter.target("left", self.indices, self.position, self.quaternion)

    def test_official_controller_receives_absolute_batched_goal_and_selected_dofs(self):
        target = self.target()
        c = self.created[0]
        self.assertTrue(torch.equal(c.config["dof_idx"], self.indices))
        self.assertEqual(c.config["mode"], "absolute_pose")
        self.assertIsNone(c.config["command_input_limits"])
        self.assertIsNone(c.config["command_output_limits"])
        self.assertEqual(c.config["link_name"], "left_eef")
        c.add_member.assert_called_once_with("/World/robot")
        goal = c.compute_control.call_args.args[0]
        self.assertTrue(torch.equal(goal["target_pos"], self.position[None]))
        self.assertEqual(tuple(goal["target_ori_mat"].shape), (1, 3, 3))
        self.assertTrue(torch.allclose(target, torch.tensor([.1, .2, .3])))
        self.assertEqual(self.adapter.calls, 1)
        c.step.assert_not_called()  # no second driver writes to the Isaac view

    def test_torso_permission_and_arm_changes_do_not_reuse_wrong_controller_group(self):
        self.target()
        self.target()
        self.assertEqual(len(self.created), 1)
        self.adapter._controller("left", torch.tensor([7, 8]))
        self.adapter._controller("right", self.indices)
        self.assertEqual(len(self.created), 3)

    def test_invalid_official_output_fails_without_custom_solver_fallback(self):
        self.target()
        c = self.created[0]
        for bad in (torch.tensor([[float("nan"), 0., 0.]]), torch.zeros(1, 2)):
            c.compute_control.return_value = bad
            with self.assertRaises(SkillError) as error:
                self.target()
            self.assertEqual(error.exception.code, "official_ik_invalid_output")
        self.assertEqual(self.adapter.calls, 1)


if __name__ == "__main__":
    unittest.main()
