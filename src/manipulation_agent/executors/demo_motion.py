"""Optional, recorded robot motion around the existing ideal state operations.

The model's RGB point remains the only target. Robot Jacobians are private motor geometry;
it does not inspect the task, choose an alternate object, or certify contact.
"""
import json

from ..contracts import SkillError
from ..records import now


def eased_positions(start, finish, count):
    """A bounded Cartesian progress profile with zero velocity at either end."""
    if count < 1:
        raise ValueError('Motion must contain at least one control step')
    for index in range(1, count + 1):
        phase = index / count
        weight = phase * phase * (3 - 2 * phase)
        yield start + (finish - start) * weight


class DemoMotion:
    def _demo_joint_indices(self, arm):
        return self.torch.cat((self.robot.trunk_control_idx,
                               self.robot.arm_control_idx[arm]))

    def _demo_record(self, **data):
        with (self.output / 'demo_motion.jsonl').open('a') as stream:
            stream.write(json.dumps({'at': now(), 'env_step': self.steps,
                                     'audience': 'executor_private', **data}) + '\n')

    def _demo_jacobian(self, arm):
        """Return the selected hand's world-position Jacobian for its arm joints."""
        view = self.robot._articulation_view._physics_view
        paths = view.link_paths[0]
        names = [path.split('/')[-1] for path in paths]
        link = names.index(self.robot.eef_link_names[arm])
        jacobian = self.robot.get_jacobian()
        if jacobian.shape[0] == len(names)-1:
            link -= 1
        if link < 0 or link >= jacobian.shape[0]:
            raise ValueError('End-effector is absent from the articulation Jacobian')
        joints = self.robot.get_joint_positions()
        offset = jacobian.shape[-1] - len(joints)
        if offset not in (0, 6):
            raise ValueError('Articulation Jacobian does not match robot joints')
        columns = (self._demo_joint_indices(arm) + offset).to(device=jacobian.device)
        return jacobian[link, :3, columns]

    def _demo_reach(self, point, max_steps, *, anchor=None, arm=None, action='reach',
                    support_payload=None):
        """Move a visible arm over real env steps; return the consumed step count."""
        if not self.demo_motion:
            return 0
        if max_steps < 9:
            raise SkillError('action_timeout','Not enough control steps for visible arm motion')
        candidates = ([arm] if arm is not None else sorted(self.robot.arm_names,
            key=lambda candidate: float(self.torch.linalg.norm(
                self.robot.eef_links[candidate].get_position_orientation()[0]-point))))
        chosen = None
        for candidate in candidates:
            if candidate not in self.robot.arm_names:
                continue
            try:
                jacobian = self._demo_jacobian(candidate)
                if jacobian.shape[1] != len(self._demo_joint_indices(candidate)):
                    raise ValueError('Reach Jacobian width differs from controlled joint count')
            except (AttributeError, RuntimeError, ValueError, KeyError) as exc:
                self._demo_record(action=action, status='jacobian_error', arm=candidate,
                                  error_type=type(exc).__name__, error=str(exc))
                continue
            chosen = candidate
            break
        if chosen is None:
            self._demo_record(action=action, status='unreachable',
                              target_point=point.tolist())
            raise SkillError('demo_motion_unavailable','Robot arm geometry is unavailable for this action')
        steps = min(42, max_steps // 2)
        indices = self._demo_joint_indices(chosen)
        start = self.robot.get_joint_positions()[indices].clone()
        trunk_indices = self.robot.trunk_control_idx
        trunk_home = self._demo_trunk_home.to(device=start.device)
        arm_indices = self.robot.arm_control_idx[chosen]
        arm_start = self.robot.get_joint_positions()[arm_indices].clone()
        arm_reset = self.robot.reset_joint_pos[arm_indices].to(device=start.device)
        prepose_steps = min(12, steps // 3) if float(self.torch.linalg.norm(arm_start-arm_reset)) < .25 else 0
        self._demo_arm = chosen
        self._demo_focus = point.clone()
        self._spectator_anchor = None
        with self._anchored_operation(anchor):
            if prepose_steps:
                ready = self.robot.untucked_default_joint_pos[arm_indices].to(device=start.device)
                for values in eased_positions(arm_start, ready, prepose_steps):
                    joints = self.robot.get_joint_positions().clone()
                    joints[arm_indices] = values
                    self._base_target['posture'] = joints[self._base_target['indices']].clone()
                    self._step(self.robot.q_to_action(joints))
                    if support_payload:
                        self._relocate_contents(anchor,support_payload)
            first_hand = self.robot.eef_links[chosen].get_position_orientation()[0].clone()
            for fraction in eased_positions(0., 1., steps-prepose_steps):
                hand = self.robot.eef_links[chosen].get_position_orientation()[0]
                jacobian = self._demo_jacobian(chosen)
                desired = first_hand + (point-first_hand)*fraction
                error = (desired-hand).to(device=jacobian.device,dtype=jacobian.dtype)
                damping = .06
                matrix = jacobian @ jacobian.T + damping*damping*self.torch.eye(3,device=jacobian.device)
                delta = jacobian.T @ self.torch.linalg.solve(matrix,error)
                delta = delta.clamp(-.12,.12)
                joints = self.robot.get_joint_positions().clone()
                joints[indices] += delta.to(device=joints.device,dtype=joints.dtype)
                # The position-only Jacobian can otherwise satisfy a distant
                # point by folding the entire torso across a work surface.
                # Keep the presentation posture near the robot's reset pose.
                joints[trunk_indices] = self.torch.maximum(
                    self.torch.minimum(joints[trunk_indices], trunk_home + .55),
                    trunk_home - .55)
                joints[indices] = self.torch.maximum(
                    self.torch.minimum(joints[indices], self.robot.joint_upper_limits[indices]),
                    self.robot.joint_lower_limits[indices])
                self._base_target['posture'] = joints[self._base_target['indices']].clone()
                self._step(self.robot.q_to_action(joints))
                if support_payload:
                    self._relocate_contents(anchor,support_payload)
        finish = self.robot.get_joint_positions()[indices]
        final_hand = self.robot.eef_links[chosen].get_position_orientation()[0]
        residual=float(self.torch.linalg.norm(final_hand-point))
        self._demo_last_reach_error=residual
        self._demo_record(action=action, status='shown' if residual<=.15 else 'partial_reach', arm=chosen, steps=steps,
                          target_point=point.tolist(),
                          prepose_steps=prepose_steps,
                          joint_delta=float(self.torch.linalg.norm(finish - start)),
                          trunk_start=start[:len(trunk_indices)].tolist(),
                          trunk_finish=finish[:len(trunk_indices)].tolist(),
                          hand_target_error_m=residual)
        return steps

    def _demo_transport_pose(self, max_steps):
        """Bring a newly held object back toward the robot before driving."""
        if not self.demo_motion or self._demo_arm is None or max_steps < 16:
            return 0
        steps = min(24, max_steps)
        arm = self._demo_arm
        indices = self._demo_joint_indices(arm)
        joints = self.robot.get_joint_positions().clone()
        start = joints[indices].clone()
        joints[self.robot.trunk_control_idx] = self._demo_trunk_home.to(device=joints.device)
        arm_indices = self.robot.arm_control_idx[arm]
        joints[arm_indices] = self.robot.untucked_default_joint_pos[arm_indices].to(device=joints.device)
        finish = joints[indices].clone()
        with self._anchored_operation():
            for values in eased_positions(start, finish, steps):
                current = self.robot.get_joint_positions().clone()
                current[indices] = values
                self._base_target['posture'] = current[self._base_target['indices']].clone()
                self._step(self.robot.q_to_action(current))
        self._demo_record(action='transport_pose', status='shown', arm=arm, steps=steps,
                          joint_delta=float(self.torch.linalg.norm(finish-start)))
        return steps
