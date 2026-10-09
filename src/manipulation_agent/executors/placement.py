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

    def _surface_pose(self, held, target, point, yaw_degrees=None):
        """Try the selected surface using upstream cuboid collision checks first."""
        from omnigibson.utils import sampling_utils as S
        from omnigibson.utils import transform_utils as T
        from omnigibson.object_states import OnTop
        torch = self.torch
        _, _, extents, bb_pos = held.get_base_aligned_bbox()
        if yaw_degrees is not None:
            # Conservative horizontal envelope contains every requested yaw.
            extents=extents.clone();radius=torch.linalg.norm(extents[:2]);extents[:2]=radius
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
                    if yaw_degrees is not None:
                        import math
                        orientation=T.quat_multiply(T.euler2quat(torch.tensor([0.,0.,math.radians(yaw_degrees)])),held.get_position_orientation()[1])
                    matrix = T.pose2mat((center + torch.tensor([0.,0.,.02]), orientation)) @ T.pose_inv(
                        T.pose2mat((bb_pos, torch.tensor([0.,0.,0.,1.]))))
                    self._placement_record({'status':'sampled','method':'selected_surface_cuboid',
                        'target':target.name,'held':held.name,'selected_point':point.tolist(),
                        'ray_height_m':height,'held_bbox_extent':extents.tolist()})
                    return T.mat2pose(matrix)
            # A model-selected surface is an actuator constraint. Never widen
            # it to the whole object (e.g. another shelf or a fridge roof).
            raise SkillError('sampling_error','No collision-free pose near the selected surface; choose another point on that surface')
        # Legacy callers without a selected surface may request object-wide placement.
        if yaw_degrees is not None:
            raise SkillError('sampling_error','No selected-surface pose satisfying the requested orientation')
        pose = self.primitives._sample_pose_with_object_and_predicate(OnTop, held, target)
        self._placement_record({'status':'sampled','method':'official_object_surface_sampler',
                                'target':target.name,'held':held.name,'held_bbox_extent':extents.tolist()})
        return pose

    def _checked_place_on_top(self, target, max_steps, point=None, yaw_degrees=None):
        from omnigibson.object_states import OnTop
        from omnigibson.action_primitives.action_primitive_set_base import ActionPrimitiveError
        held = self._get_held()
        if held is None:
            raise SkillError('empty_hand','No object is held')
        check = self._try_place_on_top(held, target, max_steps, point, yaw_degrees)
        self.frames_revision = -1
        return {'primitive':'place_on_top','implementation':'strict_selected_surface_placement',
                'postcondition':check,'failure_policy':'restore_pre_action_state'}

    def _try_place_on_top(self, held, target, max_steps, point, yaw_degrees=None):
        from omnigibson.object_states import OnTop, Touching, VerticalAdjacency
        from omnigibson.action_primitives.action_primitive_set_base import ActionPrimitiveError
        with self._placement_context():
            try:
                pose = self._surface_pose(held, target, point, yaw_degrees)
            except ActionPrimitiveError as exc:
                raise SkillError('sampling_error',str(exc)) from exc
            # Do not call upstream _release(): it runs physics while the object
            # is still next to the hand, BEFORE teleporting to the destination.
            contents=list(self._carry_contents) if self.ideal_carry else []
            dependencies=list(getattr(self,'_carry_dependencies',[]))
            self._carry_detach()
            held.set_position_orientation(*pose)
            self._relocate_contents(held,contents)
            held.keep_still()
            for _ in range(min(50,max_steps)):
                self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
            adjacency = held.states[VerticalAdjacency].get_value()
            touching = bool(held.states[Touching].get_value(target))
            official_on_top = bool(held.states[OnTop].get_value(target))
            supported, support = self._selected_surface_support(held, target, point, touching,
                                                               [obj for obj, _ in contents])
            self._placement_record({'status':'postcondition_check','target':target.name,'held':held.name,
                'position':held.get_position_orientation()[0].tolist(),
                'touching':touching,'official_on_top':official_on_top,'selected_surface_support':support,
                'target_below':target in adjacency.negative_neighbors,
                'target_above':target in adjacency.positive_neighbors,
                'grasp_released':self._get_held() is None})
            # Official OnTop alone says nothing about which shelf was selected.
            if not (supported if point is not None else official_on_top):
                raise SkillError('postcondition_error','Object is not stably supported by the selected surface',changed=True)
            self._verify_payload(dependencies)
        return 'selected_surface_geometric_support_after_settling' if point is not None else 'official_OnTop'

    def _selected_surface_support(self, held, target, point, touching, payload=()):
        """An actual lower shelf can support an object while official OnTop is false.

        This motor check is independent of task goals. It never writes predicates
        or changes evaluator semantics, and does not accept mere side contact.
        """
        from omnigibson.utils.sampling_utils import raytest
        if point is None:
            return False, {'available':False,'reason':'No selected surface point'}
        lo,hi=held.aabb;center=(lo+hi)/2
        end=center.clone();end[2]=lo[2]-.08
        # A shallow plate's AABB center can lie inside its food's collision
        # proxy. The ray checks the support UNDER the entire carried assembly;
        # internal payload contact must not hide the actual supporting surface.
        ignored=[link.prim_path for obj in (held,self.robot,*payload) for link in obj.links.values()]
        hit=raytest(center,end,ignore_bodies=ignored)
        target_paths={link.prim_path for link in target.links.values()}
        speed=float(self.torch.linalg.norm(held.get_linear_velocity()))
        gap=float(lo[2]-hit['position'][2]) if hit['hit'] else None
        normal_z=float(hit['normal'][2]) if hit['hit'] else None
        height_error=abs(float(hit['position'][2]-point[2])) if hit['hit'] else None
        selected_xy_distance=float(self.torch.linalg.norm(center[:2]-point[:2]))
        # The contact predicate can be false for a motionless object whose
        # bottom is flush with the selected support (archived cabinet-door
        # placement: 6e-8 m gap). Keep it as evidence, not a second veto on the
        # geometric support check. Wrong surfaces and side contact still fail.
        checks={'released':self._get_held() is None,
                'support_ray_hits_selected_object':hit.get('rigidBody') in target_paths,
                'upward_support':normal_z is not None and normal_z>=.9,
                'bottom_near_support':gap is not None and -.03<=gap<=.06,
                'selected_shelf_height':height_error is not None and height_error<=.05,
                'selected_surface_neighborhood':selected_xy_distance<=.20,'settled':speed<=.10}
        return all(checks.values()), {'checks':checks,'touching_selected_object':touching,
            'speed_m_s':speed,'bottom_gap_m':gap,
            'normal_z':normal_z,'selected_height_error_m':height_error,'selected_xy_distance_m':selected_xy_distance,
            'support_hit_body':hit.get('rigidBody'),'ignored_payload_names':[obj.name for obj in payload]}

    def _checked_place_inside(self, target, max_steps):
        from omnigibson.object_states import Inside
        held = self._get_held()
        if held is None:
            raise SkillError('empty_hand','No object is held')
        fillable = [link for link in target.links.values() if link.is_meta_link and
                    link.meta_link_type in {'fillable','openfillable'}]
        if not fillable or Inside not in held.states:
            raise SkillError('unsupported_relation','Target has no supported fillable volume')
        start=time.monotonic();before=self.sampling_physics_steps
        residents=self._container_payload(target)
        resident_count=len(residents)
        settling_payload=[]
        with self._placement_context(target):
            contents=list(self._carry_contents) if self.ideal_carry else []
            dependencies=list(getattr(self,'_carry_dependencies',[]))
            orientation=held.get_position_orientation()[1].clone()
            self._carry_detach()
            # Capture the anchored physics wrapper installed by the context.
            original_step=self.og.sim.step_physics
            def preserve_payload_poses():
                if residents:self._relocate_container_payload(residents)
                if settling_payload:self._relocate_container_payload(settling_payload)
            def preserve_residents(*args,**kwargs):
                # The near-bin arm can push an earlier item out while the
                # official sampler or the settling steps advance physics.
                # Existing contents are part of the destination's committed
                # state; keep their link-relative poses until this placement
                # has been checked. The new item is free during sampling, then
                # kept at its accepted pose during settling.
                preserve_payload_poses()
                try:return original_step(*args,**kwargs)
                finally:
                    preserve_payload_poses()
            def bounded_step(*args,**kwargs):
                deadline=getattr(self,'deadline',None)
                if deadline is not None:deadline.check(changed=True)
                if not (deadline is not None and deadline.managed) and (self.sampling_physics_steps-before>=min(6000,max_steps*4) or time.monotonic()-start>120):
                    raise SkillError('sampling_budget_exhausted','Volume sampler exceeded physics/time limit',changed=True)
                self.sampling_physics_steps+=1
                if contents:
                    # Randomly flipping a plate is not a valid way to place
                    # its food. Preserve the carried assembly's orientation.
                    held.set_position_orientation(held.get_position_orientation()[0],orientation)
                self._relocate_contents(held,contents)
                return preserve_residents(*args,**kwargs)
            self.og.sim.step_physics=bounded_step
            from omnigibson.utils.usd_utils import RigidContactAPI
            original_contact=RigidContactAPI.is_in_contact
            def assembly_contact(scene_idx,query_set,with_set,ignore_set,current_only):
                if contents and len(query_set)==1 and next(iter(query_set)) is held and with_set is None:
                    assembly=[held,*[obj for obj,_ in contents]]
                    return original_contact(scene_idx,assembly,None,[*(ignore_set or []),*assembly],current_only)
                return original_contact(scene_idx,query_set,with_set,ignore_set,current_only)
            if contents:RigidContactAPI.is_in_contact=assembly_contact
            sampling_done=False
            try:
                sampled=held.states[Inside].set_value(target,True)
                sampling_done=True
            finally:
                if contents:RigidContactAPI.is_in_contact=original_contact
                self.og.sim.step_physics=preserve_residents if sampling_done else original_step
                self.frames_revision=-1
            try:
                if not sampled:
                    raise SkillError('sampling_error','Official volume sampler could not find a valid placement',changed=True)
                # Once the official sampler has found an Inside pose, retain
                # that link-relative pose while the action's settling ticks
                # run. Otherwise contact with the nearby robot may eject the
                # newly placed object before the postcondition is checked.
                settling_payload.extend(record for record in self._container_payload(target)
                                        if record[0] is held)
                self._relocate_contents(held,contents)
                for _ in range(min(50,max_steps)):
                    # Environment.step does not dispatch through the patched
                    # sim.step_physics method used by the official sampler.
                    preserve_payload_poses()
                    try:self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
                    finally:preserve_payload_poses()
            finally:
                self.og.sim.step_physics=original_step
            if not held.states[Inside].get_value(target):
                raise SkillError('postcondition_error','Object left container after settling',changed=True)
            self._verify_container_payload(target,residents)
            self._verify_payload(dependencies)
            if any(Inside not in obj.states or not obj.states[Inside].get_value(target) for obj,_ in contents):
                raise SkillError('postcondition_error','Carried contents do not fit inside the selected container',changed=True)
        return {'primitive':'place_inside','implementation':'transactional_official_Inside_with_rigid_payload_sampling',
                'postcondition':'Inside.get_value_after_settling','failure_policy':'restore_pre_action_state',
                'target_root_anchored':True,'existing_containment_verified':resident_count,
                'sampling_physics_steps':self.sampling_physics_steps-before}
