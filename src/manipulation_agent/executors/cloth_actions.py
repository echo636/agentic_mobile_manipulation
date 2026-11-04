"""Ideal cloth folding: set one configuration, then leave motion to physics."""
import math

from ..contracts import SkillError


def folded_configuration(points, current, contact_offset, *, support_height=None, heading=0.):
    """Bend a particle configuration in half without changing its topology.

    The finite-radius crease retains arc length and separates the two layers.
    Anchor the result at the cloth's current footprint and supporting height.
    Input particle arrays are never modified.
    """
    import torch

    # Fold along a material axis. World-axis folds become diagonal folds when
    # a rectangular cloth is rotated on the table, leaving its long span intact.
    cosine, sine = math.cos(heading), math.sin(heading)
    rotation = points.new_tensor([[cosine, -sine, 0.], [sine, cosine, 0.], [0., 0., 1.]])
    points = points @ rotation
    lower, upper = points.amin(0), points.amax(0)
    center = (lower + upper) * .5
    axis = int(torch.argmax((upper - lower)[:2]))
    radius = max(float(contact_offset) * 1.1, float(upper[2] - lower[2]) * .5 + .002)
    half_band = math.pi * radius * .5
    offset = points[:, axis] - center[axis] + half_band
    height = points[:, 2] - center[2]
    angle = (offset / radius).clamp(0., math.pi)
    bend = (offset > 0) & (offset < math.pi * radius)
    top = offset >= math.pi * radius
    result = points.clone()
    result[bend, axis] = center[axis] - half_band + (radius - height[bend]) * torch.sin(angle[bend])
    result[bend, 2] = center[2] + radius - (radius - height[bend]) * torch.cos(angle[bend])
    result[top, axis] = center[axis] - half_band - (offset[top] - math.pi * radius)
    result[top, 2] = center[2] + 2 * radius - height[top]
    result = result @ rotation.T
    result[:, :2] += ((current.amin(0)[:2] + current.amax(0)[:2]) -
                     (result.amin(0)[:2] + result.amax(0)[:2])) * .5
    height = current[:, 2].min() if support_height is None else support_height
    result[:, 2] += height - result[:, 2].min() + .002
    return result


def separate_cloth_layers(configuration, neighbors, contact_offset):
    """Release a folded sheet above intersecting cloth, never through its layers.

    Only translate the selected configuration; native physics settles the stack.
    Nearby particle columns keep an unrelated high corner from lifting the fold.
    """
    result = configuration.clone()
    low, high = result.amin(0), result.amax(0)
    for points, offset in sorted(neighbors, key=lambda row: float(row[0][:, 2].min())):
        gap = float(contact_offset) + float(offset)
        nearby = ((points[:, :2] >= low[:2] - gap) &
                  (points[:, :2] <= high[:2] + gap)).all(dim=1)
        if not bool(nearby.any()):
            continue
        bottom, top = points[nearby, 2].min(), points[nearby, 2].max()
        if top + gap <= low[2] or bottom - gap >= high[2]:
            continue
        lift = top + gap - low[2]
        result[:, 2] += lift
        low[2] += lift
        high[2] += lift
    return result


class CheckedClothActions:
    def _place_cloth_surface(self, primitive, held, target, max_steps, point, yaw_degrees=None,
                             *, roll_degrees=None):
        """Release cloth once; do not rewind its particles and native attachment.

        Rigid placement's speculative scene restores reload particle positions
        and recreate grasp constraints. Cloth instead follows the same single
        state change / ordinary physics contract as folding and placing under.
        """
        from omnigibson.object_states import NextTo, OnTop

        contents = list(self._carry_contents)
        payload = [child for child, _ in contents]
        candidates = (self._next_to_candidates(held, target, point, payload, yaw_degrees)
                      if primitive == 'place_next_to' else
                      self._surface_candidates(held, target, point, yaw_degrees, payload,
                                               roll_degrees=roll_degrees, center_footprint=True))
        if not candidates:
            raise SkillError('placement_surface_unavailable', 'No receiving surface near the selected target')
        candidate = candidates[0]
        support = candidate.get('support', target)
        self._placement_record(dict(status='cloth_release', primitive=primitive,
            held=held.name, target=target.name, support=support.name,
            position=candidate['pose'][0].tolist(), candidate=candidate['index']))
        try:
            self._carry_detach()
            held.set_position_orientation(*candidate['pose'])
            held.keep_still()
            self._relocate_contents(held, contents)
            self._placement_record(dict(status='cloth_pose_written', held=held.name))
            for _ in range(min(50, max_steps)):
                self._step(self.primitives._empty_action())
            checks = dict(released=self._get_held() is None,
                          on_top=bool(held.states[OnTop].get_value(support)))
            if primitive == 'place_next_to':
                checks['next_to'] = bool(held.states[NextTo].get_value(target))
            self._placement_record(dict(status='cloth_settled', primitive=primitive,
                held=held.name, target=target.name, support=support.name, checks=checks))
            if not all(checks.values()):
                supported = checks['released'] and checks['on_top']
                raise SkillError('postcondition_error',
                    'Cloth placement did not satisfy: ' + ', '.join(key for key, ok in checks.items() if not ok),
                    changed=True, stage='released_supported_not_adjacent' if supported else None)
        finally:
            self.frames_revision = -1
        return dict(primitive=primitive, implementation='single_cloth_release_then_native_physics',
                    postcondition='native_surface_relation_after_settling',
                    failure_policy='retain_physical_result')

    def _fold_preconditions(self, target):
        from omnigibson.object_states import Folded
        from omnigibson.utils.constants import PrimType

        if self._get_held() is not None:
            raise SkillError('hand_occupied', 'Release the carried object before folding')
        if target.prim_type != PrimType.CLOTH or Folded not in target.states:
            raise SkillError('pre_condition_error', 'Select a cloth object to fold')
        return target.states[Folded]

    def _checked_fold(self, target, max_steps):
        from omnigibson.utils.constants import PrimType
        from trimesh.bounds import oriented_bounds_2D

        folded = self._fold_preconditions(target)
        if folded.get_value():
            return {'primitive': 'fold', 'postcondition': 'already_folded'}
        settling_steps = math.ceil(8. / self.og.sim.get_sim_step_dt())
        if max_steps < settling_steps:
            raise SkillError('action_timeout', 'Insufficient steps for folding and native settling')
        cloth = target.root_link
        current = cloth.compute_particle_positions()
        # Deform the actual sheet along its current edges. Rest points and the
        # object's transform need not match a cloth that has settled or slid.
        frame, _ = oriented_bounds_2D(current[:, :2].cpu().numpy())
        heading = math.atan2(float(frame[0, 1]), float(frame[0, 0]))
        # A dangling corner can lie below the table. Anchoring to that corner
        # would construct the whole folded cloth beneath its actual support.
        contacts = cloth.get_contacts()
        support_height = max(float(point[2]) for _, point in contacts) if contacts else None
        result = folded_configuration(current, current, cloth.cloth_system.particle_contact_offset,
                                      support_height=support_height, heading=heading)
        neighbors = [(obj.root_link.compute_particle_positions(), obj.root_link.cloth_system.particle_contact_offset)
                     for obj in self.env.scene.object_registry('prim_type', PrimType.CLOTH, [])
                     if obj is not target]
        before_z = float(result[:, 2].min())
        result = separate_cloth_layers(result, neighbors, cloth.cloth_system.particle_contact_offset)
        self._placement_record(dict(status='cloth_fold_configuration', target=target.name,
            configuration_input='current_particles', heading_source='current_footprint_edges',
            support_height=support_height, material_heading=heading, previous_min_z=float(current[:, 2].min()),
            cloth_layer_clearance_lift_m=float(result[:, 2].min()) - before_z,
            configured_z=[float(result[:, 2].min()), float(result[:, 2].max())]))
        cloth.set_particle_positions(result)
        cloth.particle_velocities = self.torch.zeros_like(result)
        for _ in range(settling_steps):
            self._step(self.primitives._empty_action())
        if not folded.get_value():
            raise SkillError('postcondition_error', 'Cloth did not remain folded after settling', changed=True)
        return {'primitive': 'fold', 'implementation': 'single_cloth_configuration_then_native_physics',
                'postcondition': 'Folded.get_value_after_settling', 'settling_steps': settling_steps}
