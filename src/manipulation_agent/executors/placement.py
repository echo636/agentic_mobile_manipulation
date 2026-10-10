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
        from omnigibson.object_states import NextTo, OnTop, Touching, VerticalAdjacency
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
        # A selected pixel can lie well inside a broad table footprint. Search
        # beyond its edge as well as near the pixel; official NextTo remains
        # the acceptance criterion for every floor-supported candidate.
        candidates = []
        for radius in (.12, .22, .34, .48, .65, .85, 1.05, 1.25):
            for angle in (0, 45, 90, 135, 180, 225, 270, 315):
                theta = math.radians(angle)
                xy = point[:2] + self.torch.tensor([radius * math.cos(theta),
                    radius * math.sin(theta)], device=point.device)
                candidates.append((radius,angle,xy))
        target_lo,target_hi=target.aabb
        half=(hi-lo)[:2]/2
        # A model may click a table leg or a corner well away from its center.
        # Use the selected target's actual current bounds to find adjacent
        # floor, rather than assuming a short ring around that pixel suffices.
        for gap in (.03,.08,.15):
            for fraction in (.25,.5,.75):
                x=target_lo[0]+fraction*(target_hi[0]-target_lo[0])
                y=target_lo[1]+fraction*(target_hi[1]-target_lo[1])
                for cx,cy in ((target_lo[0]-half[0]-gap,y),
                              (target_hi[0]+half[0]+gap,y),
                              (x,target_lo[1]-half[1]-gap),
                              (x,target_hi[1]+half[1]+gap)):
                    xy=self.torch.stack((cx,cy))
                    delta=xy-point[:2]
                    candidates.append((float(self.torch.linalg.norm(delta)),
                        math.degrees(math.atan2(float(delta[1]),float(delta[0]))),xy))
        with self._placement_context(target):
            contents = list(self._carry_contents) if self.ideal_carry else []
            dependencies = list(getattr(self, '_carry_dependencies', []))
            self._carry_detach()
            accepted = None
            rejected={'no_floor_hit':0,'overlap':0,'not_next_to':0}
            for radius, angle, xy in candidates:
                start = self.torch.tensor([float(xy[0]), float(xy[1]), float(point[2]) + .6], device=point.device)
                end = start.clone(); end[2] = min(float(point[2]) - 1.5, -.5)
                hit = raytest(start, end, ignore_bodies=ignored)
                if (not hit['hit'] or float(hit['normal'][2]) < .9
                        or hit.get('rigidBody') not in support_by_link):
                    rejected['no_floor_hit']+=1
                    continue
                place = pose[0].clone(); place[:2] = xy
                place[2] = hit['position'][2] + bottom_offset + .003
                candidate_lo=lo+(place-pose[0]);candidate_hi=hi+(place-pose[0])
                blocked=bool(((candidate_hi>target_lo+.005)&
                              (candidate_lo<target_hi-.005)).all())
                for other in self.env.scene.objects:
                    if other in (held,target,self.robot) or other in supports or getattr(other,'fixed_base',True):
                        continue
                    other_lo,other_hi=other.aabb
                    if bool(((candidate_hi>other_lo+.005)&(candidate_lo<other_hi-.005)).all()):
                        blocked=True;break
                if blocked:
                    rejected['overlap']+=1
                    continue
                held.set_position_orientation(place, pose[1]); held.keep_still()
                self._relocate_contents(held, contents)
                if held.states[NextTo].get_value(target):
                    accepted = (radius, angle, place.clone(), support_by_link[hit['rigidBody']],
                                hit['position'].clone())
                    break
                rejected['not_next_to']+=1
            if accepted is None:
                self._placement_record({'status':'next_to_candidates_exhausted',
                    'target':target.name,'held':held.name,
                    'selected_point':point.tolist(),'rejected':rejected})
                raise SkillError('sampling_error', 'No supported floor pose satisfied NextTo near the selected parent point', changed=True)
            for _ in range(min(50, max_steps)):
                # Let gravity close the initial 3 mm clearance and create an
                # actual lawn contact. Reprojecting the egg at every env.step
                # kept its pose visually stable but made Touching stay false.
                self._relocate_contents(held, contents)
                self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
            self._relocate_contents(held, contents)
            if not held.states[NextTo].get_value(target):
                raise SkillError('postcondition_error', 'Object is no longer next to the selected target', changed=True)
            if OnTop not in held.states or not held.states[OnTop].get_value(accepted[3]):
                adjacency=held.states[VerticalAdjacency].get_value()
                self._placement_record({'status':'unsupported_next_to_candidate',
                    'target':target.name,'held':held.name,'support':accepted[3].name,
                    'candidate_position':accepted[2].tolist(),
                    'held_aabb_bottom_m':float(held.aabb[0][2]),
                    'ray_support_height_m':float(accepted[4][2]),
                    'bottom_ray_gap_m':float(held.aabb[0][2]-accepted[4][2]),
                    'support_aabb_top_m':float(accepted[3].aabb[1][2]),
                    'touching_support':bool(held.states[Touching].get_value(accepted[3])),
                    'support_below':accepted[3] in adjacency.negative_neighbors,
                    'support_above':accepted[3] in adjacency.positive_neighbors,
                    'support_on_top':False})
                raise SkillError('postcondition_error','NextTo placement is not supported by the sampled floor or lawn',changed=True)
            self._verify_payload(dependencies)
            self._placement_record({'status': 'next_to_verified', 'target': target.name,
                                    'held': held.name, 'radius_m': accepted[0], 'angle_deg': accepted[1]})
        self.frames_revision = -1
        return {'primitive': 'place_next_to', 'implementation': 'selected_parent_floor_search',
                'postcondition': 'NextTo.get_value_after_settling', 'failure_policy': 'restore_pre_action_state'}

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
            offsets = [(0., 0.)] + [(x,y) for r in (.04,.08,.12,.16,.20)
                        for x,y in ((r,0),(-r,0),(0,r),(0,-r))]
            centers = torch.stack([point + torch.tensor([x,y,0.], device=point.device)
                                   for x,y in offsets])
            for verify_empty in (True,False):
                for height in (.03, .10, .20):
                    starts = (centers + torch.tensor([0.,0.,height],device=point.device)).unsqueeze(0)
                    ends = (centers - torch.tensor([0.,0.,.08],device=point.device)).unsqueeze(0)
                    samples = S.sample_cuboid_on_object(target, starts, ends, extents,
                        ignore_objs=[held,self.robot],verify_cuboid_empty=verify_empty,
                        refuse_downwards=True, undo_cuboid_bottom_padding=True,
                        max_angle_with_z_axis=.17)
                    if samples[0][0] is None:
                        continue
                    center, _, orientation = samples[0][:3]
                    if yaw_degrees is not None:
                        import math
                        orientation=T.quat_multiply(T.euler2quat(torch.tensor([0.,0.,math.radians(yaw_degrees)])),held.get_position_orientation()[1])
                    matrix = T.pose2mat((center + torch.tensor([0.,0.,.02]), orientation)) @ T.pose_inv(
                        T.pose2mat((bb_pos, torch.tensor([0.,0.,0.,1.]))))
                    pose=T.mat2pose(matrix)
                    if not verify_empty:
                        original=held.get_position_orientation()
                        held.set_position_orientation(*pose)
                        proposed_lo,proposed_hi=held.aabb
                        held.set_position_orientation(*original)
                        if any(bool(((proposed_hi>obj.aabb[0]+.005)&
                                     (proposed_lo<obj.aabb[1]-.005)).all())
                               for obj in self.env.scene.objects if obj not in (held,target,self.robot)):
                            continue
                    self._placement_record({'status':'sampled',
                        'method':'selected_surface_cuboid' if verify_empty else 'verified_selected_surface_fallback',
                        'target':target.name,'held':held.name,'selected_point':point.tolist(),
                        'ray_height_m':height,'held_bbox_extent':extents.tolist()})
                    return pose
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
                if point is not None:
                    # Broad upholstered surfaces can eject a shallow object
                    # several metres while the release settles. Keep the
                    # selected pose through the settling window, as for
                    # verified under-furniture placements, then check the
                    # actual support after the final physics step.
                    held.set_position_orientation(*pose)
                    held.keep_still()
                    self._relocate_contents(held,contents)
                self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
                if point is not None and contents:
                    # The final settling step can dislodge a light payload
                    # from a shallow carrier even though the carrier itself
                    # remained at the verified selected surface.
                    self._relocate_contents(held,contents)
            if point is not None:
                held.keep_still()
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
            selected_checks = support.get('checks', {})
            official_selected_surface = (official_on_top and point is not None and
                selected_checks.get('selected_surface_neighborhood', False) and
                selected_checks.get('selected_shelf_height', False))
            if not ((supported or official_selected_surface) if point is not None else official_on_top):
                raise SkillError('postcondition_error','Object is not stably supported by the selected surface',changed=True)
            self._verify_payload(dependencies)
            if point is not None:
                if not hasattr(self,'_stabilized_containers'):self._stabilized_containers={}
                # A sampler may leave a shallow object just above upholstery:
                # geometrically supported, but without the contact required by
                # official OnTop. Seat it by the measured ray gap and retain
                # the motor's ideal support through subsequent control steps.
                gap=support.get('bottom_gap_m')
                if not official_on_top and gap is not None and .002<gap<=.025:
                    original=held.get_position_orientation()
                    seated=original[0].clone();seated[2]-=gap+.001
                    self._stabilized_containers[held]=(seated,original[1])
                    try:
                        for _ in range(min(4,max_steps)):
                            self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
                    except Exception:
                        self._stabilized_containers.pop(held,None)
                        raise
                    seated_ok,_=self._selected_surface_support(held,target,point,
                        bool(held.states[Touching].get_value(target)),[obj for obj,_ in contents])
                    if not seated_ok:
                        self._stabilized_containers.pop(held,None)
                        held.set_position_orientation(*original);held.keep_still()
                self._stabilized_containers[held]=tuple(v.clone() for v in held.get_position_orientation())
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

    def _fillable_grid_pose(self, held, target, fillable, contents):
        """Find a visible-volume pose when the official stochastic setter exhausts itself.

        This stays in the motor: candidates come only from the selected
        container's fillable links and must satisfy the unmodified Inside
        predicate. Avoid already placed objects before accepting a pose.
        """
        from omnigibson.object_states import Inside
        torch = self.torch
        pose = held.get_position_orientation()
        held_lo, held_hi = held.aabb
        extent = held_hi - held_lo
        center_offset = (held_lo + held_hi) / 2 - pose[0]
        movable = [obj for obj in self.env.scene.objects
                   if obj not in (held, target, self.robot, *(item for item, _ in contents))
                   and not getattr(obj, 'fixed_base', True)]
        rejected={'invalid_inset':0,'outside_volume':0,'overlap':0,'inside_false':0}
        for link in fillable:
            low, high = link.visual_aabb
            if bool((high <= low).any()):
                rejected['invalid_inset']+=1
                continue
            inset_lo, inset_hi = low + extent / 2 + .004, high - extent / 2 - .004
            if bool((inset_hi <= inset_lo).any()):
                rejected['invalid_inset']+=1
                continue
            # Thin boxes can occupy separate vertical layers of a toy box;
            # stopping at 55% of its fillable height missed the upper layer.
            # Pack against an edge first and reuse its vertical column. A
            # center-first search consumes the only contiguous area available
            # to a large board game after smaller toys have been placed.
            for fx, fy in ((.05,.05),(.95,.05),(.05,.95),(.95,.95),
                           (.05,.5),(.95,.5),(.5,.05),(.5,.95),
                           (.25,.25),(.75,.25),(.25,.75),(.75,.75),
                           (.25,.5),(.75,.5),(.5,.25),(.5,.75),(.5,.5)):
                for fz in (.18, .36, .55, .75, .90):
                    fraction = torch.tensor([fx, fy, fz], device=low.device)
                    center = inset_lo + fraction * (inset_hi - inset_lo)
                    if not bool(link.check_points_in_volume(center.unsqueeze(0))[0]):
                        rejected['outside_volume']+=1
                        continue
                    place = center - center_offset
                    displacement = place - pose[0]
                    candidate_lo, candidate_hi = held_lo + displacement, held_hi + displacement
                    if any(bool(((candidate_hi > obj.aabb[0] + .005) &
                                 (candidate_lo < obj.aabb[1] - .005)).all()) for obj in movable):
                        rejected['overlap']+=1
                        continue
                    held.set_position_orientation(place, pose[1])
                    held.keep_still()
                    self._relocate_contents(held, contents)
                    if held.states[Inside].get_value(target):
                        self._placement_record({'status': 'sampled',
                            'method': 'verified_fillable_grid_fallback',
                            'target': target.name, 'held': held.name,
                            'fillable_link': link.name, 'position': place.tolist()})
                        return True
                    rejected['inside_false']+=1
        held.set_position_orientation(*pose)
        held.keep_still()
        self._relocate_contents(held, contents)
        self._placement_record({'status':'fillable_grid_exhausted','target':target.name,
            'held':held.name,'held_extent':extent.tolist(),'rejected':rejected})
        return False

    def _repack_fillable_contents(self, held, target, fillable, residents, contents):
        """Repack a crowded selected volume, preserving every official Inside state."""
        from omnigibson.object_states import Inside
        from omnigibson.utils import transform_utils as T
        if contents or len(residents) > 8:
            return False
        objects = [child for child, _, _ in residents] + [held]
        snapshots = {obj: obj.get_position_orientation() for obj in objects}
        sizes = {obj: obj.aabb[1] - obj.aabb[0] for obj in objects}
        offsets = {obj: (obj.aabb[0] + obj.aabb[1]) / 2 - snapshots[obj][0]
                   for obj in objects}
        # The final item may be a broad, thin puzzle rather than the small
        # ball used by earlier successful episodes. A single volume-sorted
        # order can miss a feasible arrangement in this bounded bin search.
        orders = [sorted(objects, key=key) for key in (
            lambda obj: -float(sizes[obj].prod()),
            lambda obj: -float(sizes[obj][0] * sizes[obj][1]),
            lambda obj: -float(sizes[obj].max()),
            lambda obj: -float(sizes[obj][2]),
            lambda obj: float(sizes[obj].prod()),
        )]
        unique_orders = []
        seen_orders = set()
        for order in orders:
            signature = tuple(obj.name for obj in order)
            if signature not in seen_orders:
                seen_orders.add(signature)
                unique_orders.append(order)
        gap = .002
        for link in fillable:
            low, high = link.visual_aabb
            origin = low + .004
            span = high - low - .008
            if bool((span <= 0).any()):
                continue
            for order in unique_orders:
                packed = []
                nodes = 0
                def search(index):
                    nonlocal nodes
                    nodes += 1
                    if nodes > 20000:
                        return False
                    if index == len(order):
                        return True
                    obj = order[index]
                    size = sizes[obj]
                    if bool((size > span).any()):
                        return False
                    axes = [sorted({0., *(float(pos[axis] + extent[axis]) + gap
                                          for pos, extent, _ in packed)})
                            for axis in range(3)]
                    for z in axes[2]:
                        for y in axes[1]:
                            for x in axes[0]:
                                pos = self.torch.tensor([x, y, z], device=low.device)
                                if bool((pos + size > span + 1e-6).any()):
                                    continue
                                if any(bool(((pos < prior + extent + gap) &
                                             (pos + size + gap > prior)).all())
                                       for prior, extent, _ in packed):
                                    continue
                                packed.append((pos, size, obj))
                                if search(index + 1):
                                    return True
                                packed.pop()
                    return False
                if not search(0):
                    continue
                for pos, size, obj in packed:
                    center = origin + pos + size / 2
                    obj.set_position_orientation(center - offsets[obj], snapshots[obj][1])
                    obj.keep_still()
                if all(obj.states[Inside].get_value(target) for obj in objects):
                    residents[:] = [(obj, link, T.relative_pose_transform(
                        *obj.get_position_orientation(), *link.get_position_orientation()))
                        for obj in objects if obj is not held]
                    self._placement_record({'status':'sampled',
                        'method':'verified_fillable_repack_fallback',
                        'target':target.name,'held':held.name,
                        'repacked_residents':len(residents),
                        'packing_order':[obj.name for obj in order]})
                    return True
                for obj, pose in snapshots.items():
                    obj.set_position_orientation(*pose)
                    obj.keep_still()
        self._placement_record({'status':'fillable_repack_exhausted',
            'target':target.name,'held':held.name,
            'residents':[obj.name for obj, _, _ in residents],
            'object_extents':{obj.name:sizes[obj].tolist() for obj in objects},
            'orders_tried':len(unique_orders)})
        return False

    def _checked_place_inside(self, target, max_steps):
        held = self._get_held()
        if held is None:
            raise SkillError('empty_hand','No object is held')
        if held is target:
            raise SkillError('unsupported_relation','An object cannot be placed inside itself')
        from omnigibson.object_states import Inside
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
            initial_pose=tuple(v.clone() for v in held.get_position_orientation())
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
                # Demo motion has a verified grid/repack motor fallback. Do
                # not let the stochastic official setter monopolize the
                # episode while a model tool call waits for its response.
                if getattr(self,'demo_motion',False) and (
                        self.sampling_physics_steps-before>=1200 or time.monotonic()-start>90):
                    raise SkillError('sampling_budget_exhausted','Trying deterministic fillable placement',changed=True)
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
                if len(query_set)==1 and next(iter(query_set)) is held and with_set is None:
                    assembly=[held,*[obj for obj,_ in contents]]
                    # The ideal visible hand is still at the selected opening
                    # while the volume sampler runs. Ignore only this robot's
                    # own collision with the item being deposited; retain
                    # furniture and other scene contacts as sampler vetoes.
                    return original_contact(scene_idx,assembly,None,
                                            [*(ignore_set or []),*assembly,self.robot],current_only)
                return original_contact(scene_idx,query_set,with_set,ignore_set,current_only)
            RigidContactAPI.is_in_contact=assembly_contact
            sampling_done=False
            placement_method='official_Inside_volume_sampler'
            try:
                try:
                    sampled=False
                    if getattr(self,'demo_motion',False) and not contents and hasattr(held,'aabb'):
                        extent=held.aabb[1]-held.aabb[0]
                        # The stochastic setter may stand a thin board game
                        # upright after carrying it flat, consuming most of
                        # a toy box's height. Prefer a verified pose that
                        # preserves its visible grasp-time orientation.
                        if float(extent[2]) < .45 * min(float(extent[0]),float(extent[1])):
                            sampled=self._fillable_grid_pose(held,target,fillable,contents)
                            if sampled:
                                placement_method='verified_fillable_grid_flat_item'
                    if not sampled:
                        sampled=held.states[Inside].set_value(target,True)
                except SkillError as exc:
                    if exc.code!='sampling_budget_exhausted' or not getattr(self,'demo_motion',False):
                        raise
                    sampled=False
                    held.set_position_orientation(*initial_pose)
                    held.keep_still()
                    self._relocate_contents(held,contents)
                    self._placement_record({'status':'official_sampler_budget_to_verified_fallback',
                        'target':target.name,'held':held.name,
                        'sampling_physics_steps':self.sampling_physics_steps-before})
                sampling_done=True
            finally:
                RigidContactAPI.is_in_contact=original_contact
                self.og.sim.step_physics=preserve_residents if sampling_done else original_step
                self.frames_revision=-1
            try:
                if not sampled:
                    sampled=self._fillable_grid_pose(held,target,fillable,contents)
                    placement_method='verified_fillable_grid_fallback'
                    if not sampled:
                        sampled=self._repack_fillable_contents(held,target,fillable,residents,contents)
                        placement_method='verified_fillable_repack_fallback'
                    if not sampled:
                        raise SkillError('sampling_error','No verified pose in the selected fillable volume',changed=True)
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
            stabilized_payload=self._container_payload(target)
        # Robot navigation and later drawer operations can knock a previously
        # deposited item out of even a fixed cabinet. Preserve its verified
        # link-relative pose until it is deliberately grasped again.
        if hasattr(target,'get_position_orientation'):
            if not hasattr(self,'_stabilized_containers'):self._stabilized_containers={}
            self._stabilized_containers[target]=tuple(v.clone() for v in target.get_position_orientation())
            if not hasattr(self,'_stabilized_container_payloads'):self._stabilized_container_payloads={}
            self._stabilized_container_payloads[target]=stabilized_payload
        return {'primitive':'place_inside','implementation':placement_method,
                'postcondition':'Inside.get_value_after_settling','failure_policy':'restore_pre_action_state',
                'target_root_anchored':True,'existing_containment_verified':resident_count,
                'sampling_physics_steps':self.sampling_physics_steps-before}
