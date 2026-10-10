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

    def _checked_place_under(self, target, max_steps):
        """Place the carried item under the model-selected parent using OG's Under sampler."""
        from omnigibson.object_states import OnTop, Under
        from omnigibson.utils.sampling_utils import raytest
        held = self._get_held()
        if held is None:
            raise SkillError('empty_hand', 'No object is held')
        if held is target or Under not in held.states:
            raise SkillError('unsupported_relation', 'Carried object cannot be placed under this target')
        with self._placement_context(target):
            contents = list(self._carry_contents) if self.ideal_carry else []
            dependencies = list(getattr(self, '_carry_dependencies', []))
            pose = held.get_position_orientation()
            lo, hi = held.aabb
            bottom_offset = float(pose[0][2] - lo[2])
            self._carry_detach()
            sampled = held.states[Under].set_value(target, True, use_trav_map=False)
            method = 'official_Under_sampler'
            support_floor = None
            if not sampled:
                # The upstream sampler can reject a reachable floor patch
                # under low furniture. Search only within the selected
                # parent's bounds, and accept only the official relation.
                target_lo, target_hi = target.aabb
                ignored = [link.prim_path for obj in (held, target, self.robot)
                           for link in obj.links.values()]
                floors = [obj for obj in self.env.scene.objects
                          if 'floor' in str(getattr(obj,'category','')).lower()]
                floor_by_link = {link.prim_path:floor for floor in floors
                                 for link in floor.links.values()}
                for fx, fy in ((.5,.5),(.25,.5),(.75,.5),(.5,.25),(.5,.75),
                               (.25,.25),(.75,.25),(.25,.75),(.75,.75)):
                    xy = target_lo[:2] + (target_hi[:2]-target_lo[:2]) * self.torch.tensor([fx,fy], device=target_lo.device)
                    start = self.torch.tensor([float(xy[0]),float(xy[1]),float(target_hi[2])+.3],device=xy.device)
                    end = start.clone();end[2] = min(float(target_lo[2])-1.5,-.5)
                    hit = raytest(start,end,ignore_bodies=ignored)
                    if (not hit['hit'] or float(hit['normal'][2]) < .9
                            or hit.get('rigidBody') not in floor_by_link):
                        continue
                    place=pose[0].clone();place[:2]=xy;place[2]=hit['position'][2]+bottom_offset+.003
                    # Reject a second item occupying the first item's space,
                    # even if the center ray happened to reach the floor.
                    candidate_lo=lo+(place-pose[0])
                    candidate_hi=hi+(place-pose[0])
                    blocked=False
                    for other in self.env.scene.objects:
                        if other in (held,target,self.robot) or other in floors or getattr(other,'fixed_base',True):
                            continue
                        other_lo,other_hi=other.aabb
                        if bool(((candidate_hi>other_lo+.005)&(candidate_lo<other_hi-.005)).all()):
                            blocked=True;break
                    if blocked:continue
                    held.set_position_orientation(place,pose[1]);held.keep_still()
                    self._relocate_contents(held,contents)
                    if held.states[Under].get_value(target):
                        sampled=True;method='verified_floor_pose_under_selected_parent'
                        support_floor=floor_by_link[hit['rigidBody']]
                        break
            if not sampled:
                raise SkillError('sampling_error', 'No supported pose satisfied Under for the selected parent', changed=True)
            self._relocate_contents(held, contents)
            accepted_pose=held.get_position_orientation()
            for _ in range(min(50, max_steps)):
                if method != 'official_Under_sampler':
                    held.set_position_orientation(*accepted_pose);held.keep_still()
                    self._relocate_contents(held,contents)
                self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
            if method != 'official_Under_sampler':
                held.set_position_orientation(*accepted_pose);held.keep_still()
                self._relocate_contents(held,contents)
            if not held.states[Under].get_value(target):
                raise SkillError('postcondition_error', 'Object is no longer under the selected target', changed=True)
            if support_floor is not None and (OnTop not in held.states or
                    not held.states[OnTop].get_value(support_floor)):
                raise SkillError('postcondition_error','Under placement is not supported by the sampled floor',changed=True)
            self._verify_payload(dependencies)
        self.frames_revision = -1
        return {'primitive': 'place_under', 'implementation': method,
                'postcondition': 'Under.get_value_after_settling', 'failure_policy': 'restore_pre_action_state'}

    def _checked_place_next_to(self, target, max_steps, point):
        """Search floor poses near the selected parent pixel; accept only official NextTo."""
        import math
        from omnigibson.object_states import NextTo, OnTop
        from omnigibson.utils.sampling_utils import raytest
        held = self._get_held()
        if held is None:
            raise SkillError('empty_hand', 'No object is held')
        if held is target or NextTo not in held.states:
            raise SkillError('unsupported_relation', 'Carried object cannot be placed next to this target')
        if str(getattr(target,'category','')).lower() in {'floor','lawn','ground','ground_plane'}:
            raise SkillError('unsupported_relation', 'Select the visible tree or fixture itself for place_next_to')
        pose = held.get_position_orientation()
        lo, hi = held.aabb
        bottom_offset = float(pose[0][2] - lo[2])
        ignored = [link.prim_path for obj in (held, target, self.robot)
                   for link in obj.links.values()]
        supports = [obj for obj in self.env.scene.objects if any(
            name in str(getattr(obj,'category','')).lower() for name in ('floor','lawn','ground'))]
        support_by_link = {link.prim_path:obj for obj in supports
                           for link in obj.links.values()}
        candidates = [(radius, angle) for radius in (.12, .22, .34, .48)
                      for angle in (0, 45, 90, 135, 180, 225, 270, 315)]
        with self._placement_context(target):
            contents = list(self._carry_contents) if self.ideal_carry else []
            dependencies = list(getattr(self, '_carry_dependencies', []))
            self._carry_detach()
            accepted = None
            for radius, angle in candidates:
                theta = math.radians(angle)
                xy = point[:2] + self.torch.tensor([radius * math.cos(theta),
                    radius * math.sin(theta)], device=point.device)
                start = self.torch.tensor([float(xy[0]), float(xy[1]), float(point[2]) + .6], device=point.device)
                end = start.clone(); end[2] = min(float(point[2]) - 1.5, -.5)
                hit = raytest(start, end, ignore_bodies=ignored)
                if (not hit['hit'] or float(hit['normal'][2]) < .9
                        or hit.get('rigidBody') not in support_by_link):
                    continue
                place = pose[0].clone(); place[:2] = xy
                place[2] = hit['position'][2] + bottom_offset + .003
                candidate_lo=lo+(place-pose[0]);candidate_hi=hi+(place-pose[0])
                blocked=False
                for other in self.env.scene.objects:
                    if other in (held,target,self.robot) or other in supports or getattr(other,'fixed_base',True):
                        continue
                    other_lo,other_hi=other.aabb
                    if bool(((candidate_hi>other_lo+.005)&(candidate_lo<other_hi-.005)).all()):
                        blocked=True;break
                if blocked:continue
                held.set_position_orientation(place, pose[1]); held.keep_still()
                self._relocate_contents(held, contents)
                if held.states[NextTo].get_value(target):
                    accepted = (radius, angle, place.clone(), support_by_link[hit['rigidBody']])
                    break
            if accepted is None:
                raise SkillError('sampling_error', 'No supported floor pose satisfied NextTo near the selected parent point', changed=True)
            for _ in range(min(50, max_steps)):
                held.set_position_orientation(accepted[2], pose[1]); held.keep_still()
                self._relocate_contents(held, contents)
                self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
            held.set_position_orientation(accepted[2], pose[1]); held.keep_still()
            self._relocate_contents(held, contents)
            if not held.states[NextTo].get_value(target):
                raise SkillError('postcondition_error', 'Object is no longer next to the selected target', changed=True)
            if OnTop not in held.states or not held.states[OnTop].get_value(accepted[3]):
                raise SkillError('postcondition_error','NextTo placement is not supported by the sampled floor or lawn',changed=True)
            self._verify_payload(dependencies)
            self._placement_record({'status': 'next_to_verified', 'target': target.name,
                                    'held': held.name, 'radius_m': accepted[0], 'angle_deg': accepted[1]})
        self.frames_revision = -1
        return {'primitive': 'place_next_to', 'implementation': 'selected_parent_floor_search',
                'postcondition': 'NextTo.get_value_after_settling', 'failure_policy': 'restore_pre_action_state'}

    def _collision_vertices(self, held):
        """Read active physical geometry, including world scale, without a box proxy."""
        import numpy as np
        vertices = []
        for link in held.links.values():
            if getattr(link, 'is_meta_link', False) or getattr(link, 'visual_only', False):
                continue
            apis = getattr(link, '_collision_apis', ())
            if apis and not any(api.GetCollisionEnabledAttr().Get() for api in apis):
                continue
            for mesh in getattr(link, 'collision_meshes', {}).values():
                points = mesh.points.detach().cpu().numpy()
                if not len(points):
                    continue
                transform = mesh.scaled_transform.detach().cpu().numpy()
                vertices.append(points @ transform[:3, :3].T + transform[:3, 3])
        if not vertices:
            raise SkillError('unsupported_geometry', 'Carried object has no active rigid collision geometry')
        result = np.concatenate(vertices)
        if not np.isfinite(result).all():
            raise SkillError('invalid_geometry', 'Carried object collision geometry is not finite')
        return result

    def _surface_candidates(self, held, target, point, yaw_degrees=None, payload=()):
        """Propose root poses at the selected collision surface, with the actual shape.

        Surface rays ignore the moving assembly at its OLD pose. Space at the
        NEW pose is checked by PhysX after moving it there, not by a cuboid.
        """
        import math
        from .surface_geometry import rotated_surface_geometry
        from omnigibson.utils.sampling_utils import raytest
        from omnigibson.utils import transform_utils as T
        position, orientation = held.get_position_orientation()
        vertices, anchor = rotated_surface_geometry(self._collision_vertices(held),
                                                    position.detach().cpu().numpy(), yaw_degrees)
        if yaw_degrees is not None:
            rotation = T.euler2quat(self.torch.tensor([0., 0., math.radians(yaw_degrees)],
                                                      device=orientation.device))
            orientation = T.quat_multiply(rotation, orientation)
        ignored = [link.prim_path for obj in (held, self.robot, *payload) for link in obj.links.values()]
        target_paths = {link.prim_path for link in target.links.values()}
        offsets = [(0., 0.)] + [(x, y) for radius in (.04, .08, .12)
                               for x, y in ((radius, 0), (-radius, 0), (0, radius), (0, -radius))]
        candidates = []
        # Stay above contact offsets even after the first physics step, as in
        # OG's standard kinematic sampler. This is clearance, not support area.
        clearance = .025 + .5 * 9.81 * self.og.sim.get_physics_dt() ** 2
        for index, (x, y) in enumerate(offsets):
            center = point + self.torch.tensor([x, y, 0.], device=point.device)
            end = center - self.torch.tensor([0., 0., .08], device=point.device)
            attempts = []
            # Start close to the click: a ray from above the whole object can
            # hit the shelf ABOVE the selected lower shelf. Taller starts only
            # recover small visual/collision-surface offsets on this shelf.
            for height in (.03, .10, .20):
                start = center + self.torch.tensor([0., 0., height], device=point.device)
                hit = raytest(start, end, ignore_bodies=ignored)
                reason = ('no_support_surface' if not hit['hit'] else
                          'different_support_object' if hit.get('rigidBody') not in target_paths else
                          'support_not_upward' if float(hit['normal'][2]) < .9 else
                          'different_shelf_height' if abs(float(hit['position'][2]) - float(point[2])) > .05 else None)
                attempts.append({'ray_height_m': height, 'reason': reason,
                                 'support_hit_body': hit.get('rigidBody')})
                if reason is None:
                    break
            if reason:
                self._placement_record({'status': 'candidate_rejected', 'stage': 'surface_ray',
                    'candidate': index, 'reason': reason, 'target': target.name, 'held': held.name,
                    'ray_attempts': attempts})
                continue
            support = self.torch.as_tensor(hit['position'], dtype=point.dtype, device=point.device)
            anchor_tensor = self.torch.as_tensor(anchor, dtype=point.dtype, device=point.device)
            root = support - anchor_tensor
            root[2] += clearance
            candidates.append({'index': index, 'pose': (root, orientation.clone()),
                               'support_position': support.tolist(), 'offset_xy_m': [x, y],
                               'bottom_anchor_offset': anchor.tolist(), 'clearance_m': clearance})
        self._placement_record({'status': 'candidates_constructed', 'method': 'selected_surface_collision_geometry',
            'target': target.name, 'held': held.name, 'selected_point': point.tolist(),
            'yaw_degrees': yaw_degrees, 'collision_vertex_count': len(vertices),
            'rotated_geometry_extent': (vertices.max(axis=0)-vertices.min(axis=0)).tolist(),
            'bottom_anchor_offset': anchor.tolist(), 'candidate_count': len(candidates)})
        return candidates

    def _surface_pose(self, held, target, point, yaw_degrees=None):
        """Compatibility pose proposal; the caller must still check physical settling."""
        from omnigibson.object_states import OnTop
        if point is None:
            if yaw_degrees is not None:
                raise SkillError('sampling_error', 'A selected surface is required for oriented placement')
            return self.primitives._sample_pose_with_object_and_predicate(OnTop, held, target)
        payload = [obj for obj, _ in self._carry_contents] if self.ideal_carry else []
        candidates = self._surface_candidates(held, target, point, yaw_degrees, payload)
        if not candidates:
            raise SkillError('sampling_error', 'No upward surface of the selected object near the selected point')
        return candidates[0]['pose']

    def _checked_place_on_top(self, target, max_steps, point=None, yaw_degrees=None):
        held = self._get_held()
        if held is None:
            raise SkillError('empty_hand', 'No object is held')
        try:
            check = self._try_place_on_top(held, target, max_steps, point, yaw_degrees)
        finally:
            self.frames_revision = -1
        return {'primitive': 'place_on_top', 'implementation': 'selected_surface_collision_geometry_and_settling',
                'postcondition': check, 'failure_policy': 'restore_pre_action_state'}

    def _placement_contacts(self, held, payload=()):
        from omnigibson.utils.usd_utils import RigidContactAPI
        assembly = (held, *payload)
        paths = {link.prim_path for obj in assembly for link in obj.links.values()}
        pairs = RigidContactAPI.get_contact_pairs(held.scene.idx, query_set=assembly,
                                                  with_set=None, current_only=True)
        return sorted({other for _, other in pairs if other not in paths})

    def _placement_physics_step(self):
        """A hypothetical candidate must not update task metrics or record video."""
        deadline = getattr(self, 'deadline', None)
        if deadline is not None:
            deadline.check(changed=True)
        self.sampling_physics_steps += 1
        self.og.sim.step_physics()

    def _try_place_on_top(self, held, target, max_steps, point, yaw_degrees=None):
        from omnigibson.object_states import OnTop, Touching, VerticalAdjacency
        from omnigibson.action_primitives.action_primitive_set_base import ActionPrimitiveError
        with self._placement_context():
            contents = list(self._carry_contents) if self.ideal_carry else []
            payload = [obj for obj, _ in contents]
            dependencies = list(getattr(self, '_carry_dependencies', []))
            if point is None:
                try:
                    pose = self._surface_pose(held, target, point, yaw_degrees)
                except ActionPrimitiveError as exc:
                    raise SkillError('sampling_error', str(exc)) from exc
                self._carry_detach()
                held.set_position_orientation(*pose)
                self._relocate_contents(held, contents)
                held.keep_still()
            else:
                candidates = self._surface_candidates(held, target, point, yaw_degrees, payload)
                self._carry_detach()
                trial_state = self.og.sim.dump_state(serialized=False)
                accepted = None
                # One second of free physics tests actual stability, not whether
                # a rectangular footprint is 80% covered by the target.
                settle_steps = max(1, int(1.0 / self.og.sim.get_physics_dt()))
                for number, candidate in enumerate(candidates):
                    if number:
                        self.og.sim.load_state(trial_state, serialized=False)
                    held.set_position_orientation(*candidate['pose'])
                    self._relocate_contents(held, contents)
                    held.keep_still()
                    held.wake()
                    self._placement_physics_step()
                    contacts = self._placement_contacts(held, payload)
                    if contacts:
                        self._placement_record({'status': 'candidate_rejected', 'stage': 'raised_pose_collision',
                            'candidate': candidate['index'], 'target': target.name, 'held': held.name,
                            'contact_bodies': contacts, 'clearance_m': candidate['clearance_m']})
                        continue
                    for _ in range(settle_steps):
                        self._placement_physics_step()
                    contacts = self._placement_contacts(held, payload)
                    touching = any(path in contacts for path in (link.prim_path for link in target.links.values()))
                    supported, support = self._selected_surface_support(held, target, point, touching, payload)
                    if not supported:
                        self._placement_record({'status': 'candidate_rejected', 'stage': 'free_settling',
                            'candidate': candidate['index'], 'target': target.name, 'held': held.name,
                            'support': support, 'contact_bodies': contacts})
                        continue
                    accepted = candidate
                    self._placement_record({'status': 'sampled', 'method': 'selected_surface_collision_geometry',
                        'candidate': candidate['index'], 'target': target.name, 'held': held.name,
                        'selected_point': point.tolist(), 'requested_yaw_degrees': yaw_degrees,
                        'support_position': candidate['support_position'],
                        'settled_position': held.get_position_orientation()[0].tolist(),
                        'support': support, 'contact_bodies': contacts})
                    break
                if accepted is None:
                    raise SkillError('sampling_error', 'No collision-free, stable pose on the selected surface', changed=True)
            # Only the accepted physical result advances environment metrics,
            # object-state transition rules and the recorded replay.
            for _ in range(min(50, max_steps)):
                self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
            adjacency = held.states[VerticalAdjacency].get_value()
            touching = bool(held.states[Touching].get_value(target))
            official_on_top = bool(held.states[OnTop].get_value(target))
            supported, support = self._selected_surface_support(held, target, point, touching, payload)
            self._placement_record({'status': 'postcondition_check', 'target': target.name, 'held': held.name,
                'position': held.get_position_orientation()[0].tolist(),
                'touching': touching, 'official_on_top': official_on_top, 'selected_surface_support': support,
                'target_below': target in adjacency.negative_neighbors,
                'target_above': target in adjacency.positive_neighbors,
                'grasp_released': self._get_held() is None})
            if not (supported if point is not None else official_on_top):
                raise SkillError('postcondition_error', 'Object is not stably supported by the selected surface', changed=True)
            self._verify_payload(dependencies)
        return 'selected_surface_collision_support_after_free_settling' if point is not None else 'official_OnTop'

    def _selected_surface_support(self, held, target, point, touching, payload=()):
        """Check the actual lower collider patch after gravity, including lower shelves.

        No whole-box footprint, goal predicate mutation, or held-pose projection
        is used. The root/box center may lie outside a supported pan body.
        """
        from .surface_geometry import bottom_anchor
        from omnigibson.utils.sampling_utils import raytest
        if point is None:
            return False, {'available': False, 'reason': 'No selected surface point'}
        anchor = self.torch.as_tensor(bottom_anchor(self._collision_vertices(held)),
                                     dtype=point.dtype, device=point.device)
        start = anchor + self.torch.tensor([0., 0., .03], device=point.device)
        end = anchor - self.torch.tensor([0., 0., .04], device=point.device)
        ignored = [link.prim_path for obj in (held, self.robot, *payload) for link in obj.links.values()]
        hit = raytest(start, end, ignore_bodies=ignored)
        target_paths = {link.prim_path for link in target.links.values()}
        contacts = self._placement_contacts(held, payload)
        robot_paths = {link.prim_path for link in self.robot.links.values()}
        speed = float(self.torch.linalg.norm(held.get_linear_velocity()))
        angular_speed = float(self.torch.linalg.norm(held.get_angular_velocity()))
        gap = float(anchor[2] - hit['position'][2]) if hit['hit'] else None
        normal_z = float(hit['normal'][2]) if hit['hit'] else None
        height_error = abs(float(hit['position'][2] - point[2])) if hit['hit'] else None
        selected_xy_distance = float(self.torch.linalg.norm(anchor[:2] - point[:2]))
        checks = {'released': self._get_held() is None,
                  'support_ray_hits_selected_object': hit.get('rigidBody') in target_paths,
                  'upward_support': normal_z is not None and normal_z >= .9,
                  'bottom_near_support': gap is not None and -.01 <= gap <= .015,
                  'selected_shelf_height': height_error is not None and height_error <= .05,
                  'selected_surface_neighborhood': selected_xy_distance <= .20,
                  'settled': speed <= .10 and angular_speed <= .5,
                  'not_supported_by_robot': not any(path in robot_paths for path in contacts)}
        return all(checks.values()), {'checks': checks, 'touching_selected_object': touching,
            'speed_m_s': speed, 'angular_speed_rad_s': angular_speed, 'bottom_gap_m': gap,
            'normal_z': normal_z, 'selected_height_error_m': height_error,
            'selected_xy_distance_m': selected_xy_distance, 'bottom_anchor': anchor.tolist(),
            'support_hit_body': hit.get('rigidBody'), 'contact_bodies': contacts,
            'ignored_payload_names': [obj.name for obj in payload]}

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
        stabilized_payload=None
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
                # Managed benchmark episodes have one execution deadline;
                # the legacy sampler caps apply only to standalone calls.
                if not getattr(deadline,'managed',False) and (
                        self.sampling_physics_steps-before>=min(6000,max_steps*4) or time.monotonic()-start>180):
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
            if not getattr(target,'fixed_base',True):
                stabilized_payload=self._container_payload(target)
        if not getattr(target,'fixed_base',True):
            if not hasattr(self,'_stabilized_containers'):self._stabilized_containers={}
            self._stabilized_containers[target]=tuple(v.clone() for v in target.get_position_orientation())
            if not hasattr(self,'_stabilized_container_payloads'):self._stabilized_container_payloads={}
            self._stabilized_container_payloads[target]=stabilized_payload
        return {'primitive':'place_inside','implementation':'transactional_official_Inside_with_rigid_payload_sampling',
                'postcondition':'Inside.get_value_after_settling','failure_policy':'restore_pre_action_state',
                'target_root_anchored':True,'existing_containment_verified':resident_count,
                'sampling_physics_steps':self.sampling_physics_steps-before}
