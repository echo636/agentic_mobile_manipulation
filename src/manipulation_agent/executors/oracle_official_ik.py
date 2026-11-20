"""Pinned OmniGibson IK-controller adapter; simulator imports remain lazy.

The official controller computes joint position targets. Existing native position
controllers deploy them after the same measured-feedback step limiter used by the
custom DLS backend. This preserves navigation, joint-space recovery and explicit
per-operation torso permission without reloading the robot's active controllers.
"""
import hashlib
from pathlib import Path

from ..contracts import SkillError


class OfficialIKAdapter:
    def __init__(self, robot):
        from omnigibson.controllers.ik_controller import InverseKinematicsController
        from omnigibson.utils.backend_utils import _compute_backend
        import omnigibson.controllers.ik_controller as ik_module
        import omnigibson.controllers.joint_controller as joint_module
        import omnigibson.controllers.controller_base as base_module
        self.robot = robot
        self.controller_type = InverseKinematicsController
        self.compute_backend = _compute_backend
        self.controllers = {}
        self.calls = 0
        self.sources = {
            module.__name__: {"path": str(Path(module.__file__).resolve()),
                             "sha256": hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()}
            for module in (ik_module, joint_module, base_module)
        }

    def _controller(self, arm, indices):
        key = (arm, tuple(indices.detach().cpu().tolist()))
        if key not in self.controllers:
            config = dict(self.robot._default_arm_ik_controller_configs[arm])
            config.pop("name")
            config.update(
                dof_idx=indices, mode="absolute_pose",
                command_input_limits=None, command_output_limits=None,
                smoothing_filter_size=None, use_impedances=False,
                link_name=self.robot.eef_link_names[arm],
            )
            controller = self.controller_type(**config)
            controller.add_member(self.robot.articulation_root_path)
            self.controllers[key] = controller
        return self.controllers[key]

    def target(self, arm, indices, position, quaternion):
        """Use the official public compute_control API, without direct state writes."""
        cb = self.compute_backend
        controller = self._controller(arm, indices)
        # compute_control takes preprocessed absolute goals as backend arrays.
        # Do not call controller.step(): it would deploy to the shared Isaac view
        # in addition to this adapter's existing native JointControllers.
        goals = {
            "target_pos": cb.from_torch(position.unsqueeze(0)),
            "target_ori_mat": cb.T.quat2mat(cb.from_torch(quaternion.unsqueeze(0))),
        }
        output = controller.clip_control(controller.compute_control(goals))
        target = cb.to_torch(output)[0].to(position)
        if target.shape != indices.shape or not bool(target.isfinite().all()):
            raise SkillError("official_ik_invalid_output",
                             "OmniGibson IK returned non-finite or mismatched joint targets")
        self.calls += 1
        return target

    def provenance(self):
        return {
            "controller": "omnigibson.controllers.ik_controller.InverseKinematicsController",
            "entry_point": "compute_control",
            "algorithm": "OmniGibson v3.9.2 J-PARSE",
            "mode": "absolute_pose",
            "actuation": "existing native JointController after common measured-state step limiter",
            "controller_reload": False,
            "custom_dls_fallback": False,
            "calls": self.calls,
            "source": self.sources,
        }
