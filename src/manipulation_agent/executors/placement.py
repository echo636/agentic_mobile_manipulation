"""Transactional ideal placement; all geometry stays inside the motor adapter."""
import json
import time
from contextlib import contextmanager

from ..contracts import SkillError


@contextmanager
def placement_transaction(sim, record):
    """Restore the pre-action state, including the grasp, on a failed placement."""
    state = sim.dump_state(serialized=False)
    try:
        yield
    except Exception as exc:
        sim.load_state(state, serialized=False)
        record({'status': 'rolled_back', 'error_type': type(exc).__name__, 'error': str(exc)})
        raise
    else:
        record({'status': 'committed'})


class CheckedPlacement:
    def _placement_record(self, data):
        with (self.output / 'placement_diagnostics.jsonl').open('a') as stream:
            stream.write(json.dumps({'env_step': self.steps, **data}) + '\n')

    def _surface_pose(self, held, target, point):
        """Try the selected surface using upstream cuboid collision checks first."""
        from omnigibson.utils import sampling_utils as S
        from omnigibson.utils import transform_utils as T
        from omnigibson.object_states import OnTop
        torch = self.torch
        _, _, extents, bb_pos = held.get_base_aligned_bbox()
        if point is not None:
            # A short ray can reach a lower shelf; the upstream object-wide ray
            # always approaches from above the entire object's bounding box.
            offsets = [(0., 0.)] + [(x,y) for r in (.04,.08,.12)
                        for x,y in ((r,0),(-r,0),(0,r),(0,-r))]
            centers = torch.stack([point + torch.tensor([x,y,0.], device=point.device)
                                   for x,y in offsets])
            for height in (.03, .10, .20):
                starts = (centers + torch.tensor([0.,0.,height],device=point.device)).unsqueeze(0)
                ends = (centers - torch.tensor([0.,0.,.08],device=point.device)).unsqueeze(0)
                samples = S.sample_cuboid_on_object(target, starts, ends, extents,
                    refuse_downwards=True, undo_cuboid_bottom_padding=True,
                    max_angle_with_z_axis=.17)
                if samples[0][0] is not None:
                    center, _, orientation = samples[0][:3]
                    matrix = T.pose2mat((center + torch.tensor([0.,0.,.02]), orientation)) @ T.pose_inv(
                        T.pose2mat((bb_pos, torch.tensor([0.,0.,0.,1.]))))
                    self._placement_record({'status':'sampled','method':'selected_surface_cuboid',
                        'target':target.name,'held':held.name,'selected_point':point.tolist(),
                        'ray_height_m':height,'held_bbox_extent':extents.tolist()})
                    return T.mat2pose(matrix)
        # Same selected object and requested relation; no task-goal/object search.
        pose = self.primitives._sample_pose_with_object_and_predicate(OnTop, held, target)
        self._placement_record({'status':'sampled','method':'official_object_surface_sampler',
                                'target':target.name,'held':held.name,'held_bbox_extent':extents.tolist()})
        return pose

    def _checked_place_on_top(self, target, max_steps, point=None):
        from omnigibson.object_states import OnTop
        from omnigibson.action_primitives.action_primitive_set_base import ActionPrimitiveError
        held = self.primitives._get_obj_in_hand()
        if held is None:
            raise SkillError('empty_hand','No object is held')
        with placement_transaction(self.og.sim, self._placement_record):
            try:
                pose = self._surface_pose(held, target, point)
            except ActionPrimitiveError as exc:
                raise SkillError('sampling_error',str(exc)) from exc
            # Do not call upstream _release(): it runs physics while the object
            # is still next to the hand, BEFORE teleporting to the destination.
            for arm in self.robot.arm_names:
                self.robot.release_grasp_immediately(arm=arm)
            held.set_position_orientation(*pose)
            held.keep_still()
            for _ in range(min(50,max_steps)):
                self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
            if not held.states[OnTop].get_value(target):
                raise SkillError('postcondition_error','Placement did not satisfy OnTop after settling',changed=True)
        self.frames_revision = -1
        return {'primitive':'place_on_top','implementation':'checked_selected_surface_then_official_sampler',
                'postcondition':'OnTop.get_value_after_settling','failure_policy':'restore_pre_action_state'}

    def _checked_place_inside(self, target, max_steps):
        from omnigibson.object_states import Inside
        held = self.primitives._get_obj_in_hand()
        if held is None:
            raise SkillError('empty_hand','No object is held')
        fillable = [link for link in target.links.values() if link.is_meta_link and
                    link.meta_link_type in {'fillable','openfillable'}]
        if not fillable or Inside not in held.states:
            raise SkillError('unsupported_relation','Target has no supported fillable volume')
        original_step=self.og.sim.step_physics
        start=time.monotonic();before=self.sampling_physics_steps
        def bounded_step(*args,**kwargs):
            if self.sampling_physics_steps-before>=min(6000,max_steps*4) or time.monotonic()-start>120:
                raise SkillError('sampling_budget_exhausted','Volume sampler exceeded physics/time limit',changed=True)
            self.sampling_physics_steps+=1
            return original_step(*args,**kwargs)
        with placement_transaction(self.og.sim,self._placement_record):
            for arm in self.robot.arm_names:
                self.robot.release_grasp_immediately(arm=arm)
            self.og.sim.step_physics=bounded_step
            try:
                sampled=held.states[Inside].set_value(target,True)
            finally:
                self.og.sim.step_physics=original_step
                self.frames_revision=-1
            if not sampled:
                raise SkillError('sampling_error','Official volume sampler could not find a valid placement',changed=True)
            for _ in range(min(50,max_steps)):
                self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
            if not held.states[Inside].get_value(target):
                raise SkillError('postcondition_error','Object left container after settling',changed=True)
        return {'primitive':'place_inside','implementation':'transactional_official_Inside_set_value',
                'postcondition':'Inside.get_value_after_settling','failure_policy':'restore_pre_action_state',
                'sampling_physics_steps':self.sampling_physics_steps-before}
