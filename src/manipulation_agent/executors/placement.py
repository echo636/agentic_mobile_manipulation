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
        # A lower shelf can be geometrically valid but fail official OnTop:
        # the same rack is also above the shoe. Retry its official surface
        # sampler, retaining the exact selected object and official predicate.
        attempts = [point, None] if point is not None and max_steps >= 100 else [point]
        for attempt, candidate in enumerate(attempts):
            try:
                check = self._try_place_on_top(held, target, max_steps, candidate)
                break
            except SkillError:
                if attempt == len(attempts)-1:
                    raise
        self.frames_revision = -1
        self._cleanup_grasp_contacts()
        return {'primitive':'place_on_top','implementation':'checked_selected_surface_then_official_sampler',
                'postcondition':check,'failure_policy':'restore_pre_action_state'}

    def _try_place_on_top(self, held, target, max_steps, point):
        from omnigibson.object_states import OnTop, Touching, VerticalAdjacency
        from omnigibson.action_primitives.action_primitive_set_base import ActionPrimitiveError
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
            adjacency = held.states[VerticalAdjacency].get_value()
            touching = bool(held.states[Touching].get_value(target))
            official_on_top = bool(held.states[OnTop].get_value(target))
            supported, support = self._selected_surface_support(held, target, point, touching)
            self._placement_record({'status':'postcondition_check','target':target.name,'held':held.name,
                'position':held.get_position_orientation()[0].tolist(),
                'touching':touching,'official_on_top':official_on_top,'selected_surface_support':support,
                'target_below':target in adjacency.negative_neighbors,
                'target_above':target in adjacency.positive_neighbors,
                'grasp_released':self.primitives._get_obj_in_hand() is None})
            if not (official_on_top or supported):
                raise SkillError('postcondition_error','Object is not stably supported by the selected surface',changed=True)
        return 'official_OnTop' if official_on_top else 'selected_surface_contact_and_support_after_settling'

    def _selected_surface_support(self, held, target, point, touching):
        """An actual lower shelf can support an object while official OnTop is false.

        This motor check is independent of task goals. It never writes predicates
        or changes evaluator semantics, and does not accept mere side contact.
        """
        from omnigibson.utils.sampling_utils import raytest
        if point is None:
            return False, {'available':False,'reason':'No selected surface point'}
        lo,hi=held.aabb;center=(lo+hi)/2
        end=center.clone();end[2]=lo[2]-.08
        hit=raytest(center,end,ignore_bodies=[link.prim_path for obj in (held,self.robot) for link in obj.links.values()])
        target_paths={link.prim_path for link in target.links.values()}
        speed=float(self.torch.linalg.norm(held.get_linear_velocity()))
        gap=float(lo[2]-hit['position'][2]) if hit['hit'] else None
        normal_z=float(hit['normal'][2]) if hit['hit'] else None
        height_error=abs(float(hit['position'][2]-point[2])) if hit['hit'] else None
        selected_xy_distance=float(self.torch.linalg.norm(center[:2]-point[:2]))
        checks={'touching_selected_object':touching,'released':self.primitives._get_obj_in_hand() is None,
                'support_ray_hits_selected_object':hit.get('rigidBody') in target_paths,
                'upward_support':normal_z is not None and normal_z>=.9,
                'bottom_near_support':gap is not None and -.03<=gap<=.06,
                'selected_shelf_height':height_error is not None and height_error<=.05,
                'selected_surface_neighborhood':selected_xy_distance<=.20,'settled':speed<=.10}
        return all(checks.values()), {'checks':checks,'speed_m_s':speed,'bottom_gap_m':gap,
            'normal_z':normal_z,'selected_height_error_m':height_error,'selected_xy_distance_m':selected_xy_distance}

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
        self._cleanup_grasp_contacts()
        return {'primitive':'place_inside','implementation':'transactional_official_Inside_set_value',
                'postcondition':'Inside.get_value_after_settling','failure_policy':'restore_pre_action_state',
                'sampling_physics_steps':self.sampling_physics_steps-before}
