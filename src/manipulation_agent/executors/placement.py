"""Transactional ideal placement; all geometry stays inside the motor adapter."""
import json
import time
from contextlib import contextmanager, nullcontext

from ..contracts import SkillError
from .particle_payload import snapshot_particle_state, particle_state_unchanged, restore_particle_state


def inside_sample_with_fallback(native, fallback, restore, record):
    """A bounded native search must leave work available for geometric search."""
    try:
        sampled = native()
    except SkillError as exc:
        if exc.code != 'native_sampling_budget_exhausted':
            raise
        sampled = False
        record({'status': 'native_sampling_exhausted', 'error': str(exc)})
    else:
        if not sampled:
            record({'status':'native_sampling_failed', 'termination':'returned_false',
                    'fallback':'assembly_volume_search'})
    if sampled:
        return True, False
    restore()
    return fallback(), True


def scene_object_identities(sim):
    """State snapshots cannot recreate USD objects removed by transition rules."""
    scenes = getattr(sim, 'scenes', None)
    return None if scenes is None else tuple(
        (id(scene), frozenset(id(obj) for obj in scene.objects)) for scene in scenes)


@contextmanager
def placement_transaction(sim, record):
    """Restore the pre-action state, including the grasp, on a failed placement."""
    particles = snapshot_particle_state(sim)
    state = sim.dump_state(serialized=False)
    objects = scene_object_identities(sim)
    try:
        yield
    except Exception as exc:
        if (isinstance(exc, SkillError) and exc.code == 'target_changed') or not particle_state_unchanged(particles):
            # A particle system may have changed without replacing a scene
            # object. Loading the old snapshot would recreate consumed matter.
            record({'status': 'rollback_skipped', 'reason': 'target_identity_changed',
                    'error_type': type(exc).__name__})
            raise SkillError('target_changed', 'Particle or object identities changed during placement', changed=True) from exc
        if objects is not None and scene_object_identities(sim) != objects:
            record({'status': 'rollback_skipped', 'reason': 'scene_objects_replaced',
                    'error_type': type(exc).__name__})
            raise SkillError('target_changed', 'Scene objects changed during placement; inspect updated RGB',
                             changed=True) from exc
        sim.load_state(state, serialized=False)
        restore_particle_state(particles)
        record({'status': 'rolled_back', 'error_type': type(exc).__name__, 'error': str(exc)})
        raise
    else:
        record({'status': 'committed'})


class CheckedPlacement:
    def _placement_record(self, data):
        # Each newline is flushed for live monitoring. Keep the descriptor open:
        # closing every record forces costly shared-filesystem close-to-open
        # consistency work even though the next record appends to the same file.
        stream = getattr(self, '_placement_diagnostics_stream', None)
        if stream is None or stream.closed:
            stream = (self.output / 'placement_diagnostics.jsonl').open(
                'a', encoding='utf-8', buffering=1)
            self._placement_diagnostics_stream = stream
        stream.write(json.dumps({'env_step': self.steps, **data}) + '\n')

    def _close_placement_record(self):
        stream = getattr(self, '_placement_diagnostics_stream', None)
        if stream is not None:
            try:
                stream.close()
            finally:
                self._placement_diagnostics_stream = None

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

    def _next_to_candidates(self, held, target, point, payload=(), yaw_degrees=None):
        """Find upward physical surfaces near the reference, without class filters."""
        import math
        from .surface_geometry import bottom_anchor, rotated_surface_geometry
        from omnigibson.utils import transform_utils as T
        from omnigibson.utils.sampling_utils import raytest
        pose = held.get_position_orientation()
        _,anchor = rotated_surface_geometry(self._collision_vertices(held),pose[0].cpu().numpy(),yaw_degrees)
        anchor = self.torch.as_tensor(anchor, dtype=point.dtype, device=point.device)
        orientation=pose[1].clone()
        if yaw_degrees is not None:
            rotation=T.euler2quat(self.torch.tensor([0.,0.,math.radians(yaw_degrees)],device=orientation.device))
            orientation=T.quat_multiply(rotation,orientation)
        moving = (held, self.robot, *payload)
        ignored = [link.prim_path for obj in (*moving, target) for link in obj.links.values()]
        by_link = {link.prim_path: obj for obj in self.env.scene.objects
                   if all(obj is not item for item in (*moving, target)) for link in obj.links.values()}
        clearance = .025 + .5 * 9.81 * self.og.sim.get_physics_dt() ** 2
        # Use the reference's supporting surface to avoid stacking a later item
        # on an earlier one. This is geometric discovery, not a category list.
        def downward(xy, z):
            start = self.torch.tensor([float(xy[0]), float(xy[1]), z + .6], device=point.device)
            end = start.clone(); end[2] = min(z - 1.5, -.5)
            return raytest(start, end, ignore_bodies=ignored)
        reference_hits = [downward(point[:2], float(point[2]))]
        reference_bottom = bottom_anchor(self._collision_vertices(target))
        reference_hits.append(downward(reference_bottom[:2], float(reference_bottom[2])))
        reference_supports = {hit.get('rigidBody') for hit in reference_hits
                              if hit.get('hit') and float(hit['normal'][2]) >= .9
                              and hit.get('rigidBody') in by_link}
        # A support can have several links; accept its full physical object.
        reference_objects = {id(by_link[path]) for path in reference_supports}
        candidates = []
        for index, (radius, angle) in enumerate((r, a) for r in (.12, .22, .34, .48)
                                               for a in (0, 45, 90, 135, 180, 225, 270, 315)):
            theta = math.radians(angle)
            xy = point[:2] + self.torch.tensor([radius*math.cos(theta), radius*math.sin(theta)], device=point.device)
            hit = downward(xy, float(point[2]))
            support = by_link.get(hit.get('rigidBody'))
            reason = ('no_support_surface' if not hit.get('hit') else
                      'support_not_upward' if float(hit['normal'][2]) < .9 else
                      'unknown_support_body' if support is None else
                      'different_reference_support_or_occupied' if reference_objects and id(support) not in reference_objects else None)
            if reason:
                self._placement_record({'status': 'candidate_rejected', 'primitive': 'place_next_to',
                    'stage': 'surface_ray', 'candidate': index, 'reason': reason,
                    'target': target.name, 'held': held.name, 'support_hit_body': hit.get('rigidBody')})
                continue
            root = self.torch.as_tensor(hit['position'], dtype=point.dtype, device=point.device) - anchor
            root[2] += clearance
            candidates.append({'index': index, 'pose': (root, orientation.clone()), 'support': support,
                'support_position': hit['position'], 'radius_m': radius, 'angle_deg': angle,
                'clearance_m': clearance})
        return candidates

    def _next_to_check(self, held, target, support, payload=(), *, support_point=None):
        from omnigibson.object_states import NextTo, OnTop, Touching, VerticalAdjacency
        adjacency = held.states[VerticalAdjacency].get_value()
        contacts = self._placement_contacts(held, payload)
        speed = float(self.torch.linalg.norm(held.get_linear_velocity()))
        angular_speed = float(self.torch.linalg.norm(held.get_angular_velocity()))
        touching = bool(held.states[Touching].get_value(support))
        official_on_top = OnTop in held.states and bool(held.states[OnTop].get_value(support))
        # Whole-object OnTop rejects a lower shelf when the same furniture
        # also has geometry above it. Check the candidate's actual support
        # patch, just as selected-surface placement does, while retaining the
        # official relation to the adjacent item.
        supported, surface = (official_on_top, None)
        if support_point is not None:
            supported, surface = self._selected_surface_support(
                held, support, support_point, touching, payload, require_neighborhood=False)
        checks = {'next_to': bool(held.states[NextTo].get_value(target)),
                  'supported': supported,
                  'released': self._get_held() is None,
                  'settled': speed <= .10 and angular_speed <= .5}
        contact = self._placement_contact_report(held, payload, allow_sleeping=True)
        checks['no_deep_penetration'] = contact['admissible']
        return all(checks.values()), {'checks': checks,
            'contact_geometry': contact,
            'touching_support': touching, 'official_on_top': official_on_top,
            'selected_surface_support': surface,
            'support_below': support in adjacency.negative_neighbors,
            'support_above': support in adjacency.positive_neighbors,
            'contact_bodies': contacts, 'speed_m_s': speed, 'angular_speed_rad_s': angular_speed,
            'position': held.get_position_orientation()[0].tolist()}

    def _checked_place_next_to(self, target, max_steps, point, yaw_degrees=None):
        """Try complete release/settle/verify cycles; preserve preceding placements."""
        from omnigibson.object_states import NextTo
        held = self._get_held()
        if held is None:
            raise SkillError('empty_hand', 'No object is held')
        if held is target or NextTo not in held.states:
            raise SkillError('unsupported_relation', 'Carried object cannot be placed next to this target')
        try:
            with self._placement_context(target):
                contents = list(self._carry_contents) if self.ideal_carry else []
                payload = [obj for obj, _ in contents]
                dependencies = list(getattr(self, '_carry_dependencies', []))
                candidates = self._next_to_candidates(held, target, point, payload, yaw_degrees)
                self._carry_detach()
                trial_state = self.og.sim.dump_state(serialized=False)
                accepted = None
                settle_steps = max(1, int(1.0 / self.og.sim.get_physics_dt()))
                for number, candidate in enumerate(candidates):
                    if number:
                        self.og.sim.load_state(trial_state, serialized=False)
                    held.set_position_orientation(*candidate['pose'])
                    self._relocate_contents(held, contents)
                    for obj in (held, *payload):
                        obj.keep_still(); obj.wake()
                    self._placement_physics_step()
                    contacts = self._placement_contacts(held, payload)
                    if contacts:
                        contact = self._placement_contact_report(held, payload)
                        self._placement_record({'status': 'initial_contact_admitted' if contact['admissible'] else 'candidate_rejected',
                            'primitive': 'place_next_to', 'stage': 'raised_pose_collision',
                            'candidate': candidate['index'], 'target': target.name, 'held': held.name,
                            'contact_geometry': contact})
                        if not contact['admissible']:
                            continue
                    # Gravity and contacts determine the final pose. No pose
                    # reset, velocity reset, or payload projection during settling.
                    for _ in range(settle_steps):
                        self._placement_physics_step()
                    support_point = self.torch.as_tensor(candidate['support_position'],
                                                        dtype=point.dtype, device=point.device)
                    valid, details = self._next_to_check(held, target, candidate['support'], payload,
                                                         support_point=support_point)
                    if valid:
                        try:
                            self._verify_payload(dependencies)
                        except SkillError as exc:
                            if exc.code != 'postcondition_error':
                                raise
                            valid = False; details['payload_error'] = str(exc)
                    if valid:
                        # Commit the physical result to the environment/replay,
                        # then verify again before leaving the candidate loop.
                        self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
                        valid, details = self._next_to_check(held, target, candidate['support'], payload,
                                                             support_point=support_point)
                        if valid:
                            try:
                                self._verify_payload(dependencies)
                            except SkillError as exc:
                                if exc.code != 'postcondition_error':
                                    raise
                                valid = False; details['payload_error'] = str(exc)
                    self._placement_record({'status': 'next_to_verified' if valid else 'candidate_rejected',
                        'primitive': 'place_next_to', 'stage': 'free_settling', 'candidate': candidate['index'],
                        'target': target.name, 'held': held.name, 'support': candidate['support'].name,
                        'radius_m': candidate['radius_m'], 'angle_deg': candidate['angle_deg'], **details})
                    if valid:
                        accepted = candidate
                        break
                if accepted is None:
                    raise SkillError('sampling_error', 'No stable supported pose satisfied NextTo near the selected reference', changed=True)
        finally:
            self.frames_revision = -1
        return {'primitive': 'place_next_to', 'implementation': 'generic_surface_search_and_free_settling',
                'requested_yaw_degrees': yaw_degrees,
                'postcondition': 'official_NextTo_and_selected_surface_support_after_free_settling',
                'failure_policy': 'restore_pre_action_state'}

    def _attached_placement_bodies(self, root):
        if not hasattr(root,'scene'):return [root]
        from .carry import attachment_relation, support_closure
        candidates=[obj for obj in root.scene.objects if obj is not root]
        return [root,*[child for child,_,_ in support_closure(root,candidates,attachment_relation)]]

    def _collision_vertices(self, held):
        # Rigid attachments form one moving structure; a tabletop's lowest
        # collider is at its feet, while loose contents remain payload checks.
        import numpy as np
        return np.concatenate([self._object_collision_vertices(obj)
                               for obj in self._attached_placement_bodies(held)])

    def _object_collision_vertices(self, held):
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

    def _surface_candidates(self, held, target, point, yaw_degrees=None, payload=(), *, offsets=None):
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
        if offsets is None:
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
        rim_candidates = self._open_rim_candidates(held,target,point,vertices,anchor,orientation,
                          offsets,payload,clearance,start_index=len(offsets))
        # A rim click can balance an object's center just outside the box.
        # Try nearby poses with the lower patch centered inside that same
        # open cavity first; each still needs actual collision and settling.
        if any(c.get('selected_rim_surface') for c in rim_candidates):
            return rim_candidates + candidates
        return candidates + rim_candidates

    def _surface_fallback_candidates(self, held, target, point, yaw_degrees=None, payload=()):
        """Cover nearby diagonal positions and yaw after ordinary trials fail.

        The selected object, shelf and 20 cm verification neighborhood stay
        unchanged. Loaded items rotate only around gravity; an explicit yaw
        remains binding. Overlap scores order proposals without rejecting any.
        """
        import math
        from omnigibson.utils import transform_utils as T
        current = T.quat2mat(held.get_position_orientation()[1])
        reference = T.quat2mat(target.get_position_orientation()[1])
        aligned = math.degrees(math.atan2(float(reference[1, 0]), float(reference[0, 0])) -
                               math.atan2(float(current[1, 0]), float(current[0, 0])))
        angles = [yaw_degrees] if yaw_degrees is not None else [None, 90., aligned, aligned + 90.]
        offsets = [(0., 0.)] + sorted(
            [(x * .04, y * .04) for x in range(-4, 5) for y in range(-4, 5)
             if 0 < (x * .04) ** 2 + (y * .04) ** 2 <= .18 ** 2],
            key=lambda xy: xy[0] ** 2 + xy[1] ** 2)
        proposals, seen = [], set()
        for angle in angles:
            self.deadline.check()
            local = getattr(self, '_local_operation', None)
            if local is not None:
                local.check()
            for candidate in self._surface_candidates(held, target, point, angle, payload, offsets=offsets):
                position, orientation = candidate['pose']
                key = tuple(round(float(v), 5) for v in (*position, *orientation))
                if key in seen:
                    continue
                seen.add(key)
                candidate.update(index=len(proposals), proposal_yaw_degrees=angle)
                proposals.append(candidate)
        ranker = self._inside_pose_overlap_ranker(held, [held, *payload])

        def rank(candidate):
            position, orientation = candidate['pose']
            score = ranker(position.tolist(), orientation) if ranker is not None else 0.
            candidate['proposal_overlap_score'] = score
            distance = sum(v * v for v in candidate['offset_xy_m'])
            return score, round(distance, 6), abs(candidate['proposal_yaw_degrees'] or 0.)

        proposals.sort(key=rank)
        self._placement_record({'status': 'surface_proposal_coverage', 'target': target.name, 'held': held.name,
            'proposals': len(proposals), 'zero_overlap': sum(not c['proposal_overlap_score'] for c in proposals),
            'angles_degrees': angles, 'unchanged_support_and_collision_acceptance': True})
        return proposals

    def _collision_triangles(self, obj):
        """World triangles for support proposals; PhysX still verifies each hit."""
        import numpy as np
        triangles = []
        for link in obj.links.values():
            if getattr(link, 'is_meta_link', False) or getattr(link, 'visual_only', False):
                continue
            apis = getattr(link, '_collision_apis', ())
            if apis and not any(api.GetCollisionEnabledAttr().Get() for api in apis):
                continue
            for mesh in getattr(link, 'collision_meshes', {}).values():
                points = mesh.points.detach().cpu().numpy()
                faces = mesh.faces
                if faces is None:
                    # Analytic collision primitives do not expose triangles.
                    # Keep the existing vertex/raycast path for these shapes.
                    continue
                faces = faces.detach().cpu().numpy().reshape(-1, 3)
                if not len(points) or not len(faces):
                    continue
                transform = mesh.scaled_transform.detach().cpu().numpy()
                world = points @ transform[:3, :3].T + transform[:3, 3]
                triangles.append(world[faces])
        return np.concatenate(triangles) if triangles else np.empty((0, 3, 3))

    def _open_rim_candidates(self,held,target,point,vertices,anchor,orientation,
                             offsets,payload,clearance,*,start_index):
        """Propose support on the open rim surrounding the selected cavity.

        A wider item may span the rim of the selected basin or rim surface.
        This uses the SAME annotated cavity and actual upward collision faces.
        An upward obstruction rejects the extension, so a lower shelf cannot
        silently become the shelf above it. Basin clicks retain ordinary-pose
        priority; rim clicks prefer centers over the identified cavity.
        """
        from .surface_geometry import bottom_support_points
        from omnigibson.utils.sampling_utils import raytest
        volumes=[link for link in target.links.values() if link.is_meta_link
                 and link.meta_link_type in {'fillable','openfillable'}]
        if not volumes:return []
        ignored=[link.prim_path for obj in (held,self.robot,*payload) for link in obj.links.values()]
        target_paths={link.prim_path for link in target.links.values() if not link.is_meta_link}
        probes=bottom_support_points(vertices)
        proposals=[];seen=set()
        for volume in volumes:
            low,high=volume.visual_aabb
            floor_selected = abs(float(point[2]-low[2])) <= .05
            rim_selected = abs(float(point[2]-high[2])) <= .05
            if not (floor_selected or rim_selected):continue
            inside=point.clone();inside[2]=low[2]+min(.02,float(high[2]-low[2])/4)
            if floor_selected:
                if not bool(volume.check_points_in_volume(inside.reshape(1,3))[0]):continue
            else:
                # Identify an actual upward rim beside this exact cavity.
                # Bounds alone never admit a neighboring compartment.
                start=point+point.new_tensor([0.,0.,.06]);end=point-point.new_tensor([0.,0.,.06])
                selected_hit=raytest(start,end,ignore_bodies=ignored)
                if not (selected_hit.get('hit') and selected_hit.get('rigidBody') in target_paths
                        and float(selected_hit['normal'][2])>=.9
                        and abs(float(selected_hit['position'][2]-high[2]))<=.05):continue
                import math
                nearby=[inside]+[inside+inside.new_tensor([radius*math.cos(angle*math.pi/4),
                    radius*math.sin(angle*math.pi/4),0.]) for radius in (.01,.02,.04,.05) for angle in range(8)]
                if not bool(volume.check_points_in_volume(self.torch.stack(nearby)).any()):continue
            ceiling=point.clone() if floor_selected else inside.clone()
            if floor_selected:ceiling[2]=max(float(point[2])+.03,float(low[2])+.01)
            top=ceiling.clone();top[2]=high[2]+.06
            if float(top[2])<=float(ceiling[2]):continue
            if floor_selected and raytest(ceiling,top,ignore_bodies=ignored)['hit']:continue
            for dx,dy in offsets:
                center=point+point.new_tensor([dx,dy,0.])
                probe_inside=inside.clone();probe_inside[:2]=center[:2]
                if not bool(volume.check_points_in_volume(probe_inside.reshape(1,3))[0]):continue
                start=ceiling.clone();start[:2]=center[:2];end=top.clone();end[:2]=center[:2]
                if raytest(start,end,ignore_bodies=ignored)['hit']:continue
                hits=[]
                for probe in probes:
                    xy=center[:2]+point.new_tensor(probe[:2]-anchor[:2])
                    start=point.new_tensor([float(xy[0]),float(xy[1]),float(high[2])+.06])
                    end=start.clone();end[2]=min(float(point[2])-.01,float(low[2])-.01)
                    hit=raytest(start,end,ignore_bodies=ignored)
                    if (hit.get('hit') and hit.get('rigidBody') in target_paths
                            and float(hit['normal'][2])>=.9
                            and float(high[2])-.03<=float(hit['position'][2])<=float(high[2])+.05):
                        # Root height must respect the actual lower probe,
                        # not the center of a bounding box or an empty basin.
                        hits.append((float(hit['position'][2]-probe[2]),hit))
                if not hits:continue
                root_z,hit=max(hits,key=lambda item:item[0])
                root=center-point.new_tensor(anchor);root[2]=root_z+clearance
                key=tuple(round(float(v),5) for v in root)
                if key in seen:continue
                seen.add(key)
                verification=point.clone();verification[2]=float(hit['position'][2])
                proposals.append({'index':start_index+len(proposals),'pose':(root,orientation.clone()),
                    'support_position':list(map(float,hit['position'])),'offset_xy_m':[dx,dy],
                    'bottom_anchor_offset':anchor.tolist(),'clearance_m':clearance,
                    'verification_point':verification,'surface_kind':'open_fillable_rim',
                    'selected_cavity':volume.prim_path,'selected_rim_surface':not floor_selected})
        if proposals:
            self._placement_record({'status':'rim_candidates_constructed','target':target.name,'held':held.name,
                'selected_point':point.tolist(),'candidate_count':len(proposals),
                'method':'selected_open_cavity_and_actual_upward_rim_collision_faces'})
        return proposals

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

    def _placement_contact_report(self, held, payload=(), *, allow_sleeping=False):
        from .placement_contacts import contact_report
        return contact_report(self, held, payload, allow_sleeping=allow_sleeping)

    def _placement_physics_step(self):
        """A hypothetical candidate must not update task metrics or record video."""
        deadline = getattr(self, 'deadline', None)
        if deadline is not None:
            deadline.check(changed=True)
        local=getattr(self,'_local_operation',None)
        if local is not None:local.check()
        self.sampling_physics_steps += 1
        self.og.sim.step_physics()

    def _settle_surface_contacts(self, held, target, payload, max_steps, *, resting=None, payload_resting=None):
        """Finish free settling with live contacts and fresh native state reads.

        Sampling can put a small object to sleep before its contact state has
        been read. PhysX then omits current contacts, while OG's kinematic
        cache can retain a false Touching/OnTop value at the unchanged pose.
        Wake the released assembly during the final five ordinary steps;
        gravity and contacts still determine every pose and predicate.
        """
        objects = (held, target, *payload)
        steps = min(50, max_steps)
        for index in range(steps):
            if index == max(0, steps - 5):
                for obj in (held, *payload):
                    obj.wake()
                for obj in objects:
                    for state in obj.states.values():
                        state.clear_cache()
            self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
            if resting is not None:
                resting.add(*held.get_position_orientation())
            for child in payload:
                window=(payload_resting or {}).get(id(child))
                if window is not None:window.add(*child.get_position_orientation())
        for obj in objects:
            for state in obj.states.values():
                state.clear_cache()

    def _try_place_on_top(self, held, target, max_steps, point, yaw_degrees=None):
        from omnigibson.object_states import OnTop, Touching, VerticalAdjacency
        from omnigibson.action_primitives.action_primitive_set_base import ActionPrimitiveError
        from .resting_motion import RestingMotionWindow
        with self._placement_context(watch=(held, target)):
            contents = list(self._carry_contents) if self.ideal_carry else []
            payload = [obj for obj, _ in contents]
            dependencies = list(getattr(self, '_carry_dependencies', []))
            verification_point=point
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
                def candidate_stream():
                    yield from candidates
                    # Restore the detached assembly's original pose before
                    # generating alternatives. Failed trials may have tilted
                    # it; those poses must not redefine a requested yaw.
                    self.og.sim.load_state(trial_state, serialized=False)
                    start_index = 1 + max((c['index'] for c in candidates), default=-1)
                    for index, candidate in enumerate(self._surface_fallback_candidates(
                            held, target, point, yaw_degrees, payload)):
                        candidate['index'] = start_index + index
                        yield candidate

                # Start with one second of free physics. A supported body may
                # still be settling; measure its motion instead of treating
                # this first observation as a fixed stability deadline.
                settle_steps = max(1, int(1.0 / self.og.sim.get_physics_dt()))
                tried_poses = set()
                for number, candidate in enumerate(candidate_stream()):
                    pose_key = tuple(round(float(v), 5) for vector in candidate['pose'] for v in vector)
                    if pose_key in tried_poses:
                        continue
                    tried_poses.add(pose_key)
                    if number:
                        self.og.sim.load_state(trial_state, serialized=False)
                    held.set_position_orientation(*candidate['pose'])
                    self._relocate_contents(held, contents)
                    held.keep_still()
                    held.wake()
                    self._placement_physics_step()
                    contacts = self._placement_contacts(held, payload)
                    if contacts:
                        contact = self._placement_contact_report(held, payload)
                        self._placement_record({'status': 'initial_contact_admitted' if contact['admissible'] else 'candidate_rejected',
                            'stage': 'raised_pose_collision',
                            'candidate': candidate['index'], 'target': target.name, 'held': held.name,
                            'contact_geometry': contact, 'clearance_m': candidate['clearance_m']})
                        if not contact['admissible']:
                            continue
                    resting = RestingMotionWindow(self.og.sim.get_physics_dt())
                    resting.add(*held.get_position_orientation())
                    for _ in range(settle_steps):
                        self._placement_physics_step()
                        resting.add(*held.get_position_orientation())
                    contacts = self._placement_contacts(held, payload)
                    touching = any(path in contacts for path in (link.prim_path for link in target.links.values()))
                    candidate_point=candidate.get('verification_point',point)
                    supported, support = self._selected_surface_support(
                        held, target, candidate_point, touching, payload, require_contact=False, resting=resting.report())
                    elapsed_steps = settle_steps
                    extension_steps = max(1, round(.25 / self.og.sim.get_physics_dt()))
                    max_settle_steps = max(settle_steps, round(4. / self.og.sim.get_physics_dt()))
                    while (not supported and elapsed_steps < max_settle_steps
                           and support['checks'].get('settled') is False
                           and all(value for key, value in support['checks'].items() if key != 'settled')):
                        # Continue only a valid supported pose. Rolling off,
                        # changing shelves or losing the selected support ends
                        # this trial immediately; tolerances stay unchanged.
                        count = min(extension_steps, max_settle_steps-elapsed_steps)
                        for _ in range(count):
                            self._placement_physics_step()
                            resting.add(*held.get_position_orientation())
                        elapsed_steps += count
                        contacts = self._placement_contacts(held, payload)
                        touching = any(link.prim_path in contacts for link in target.links.values())
                        supported, support = self._selected_surface_support(
                            held, target, candidate_point, touching, payload, require_contact=False, resting=resting.report())
                    if elapsed_steps > settle_steps:
                        self._placement_record({'status': 'surface_settling_extended',
                            'candidate': candidate['index'], 'target': target.name, 'held': held.name,
                            'physics_seconds': elapsed_steps*self.og.sim.get_physics_dt(),
                            'supported': supported, 'support': support})
                    if not supported:
                        self._placement_record({'status': 'candidate_rejected', 'stage': 'free_settling',
                            'candidate': candidate['index'], 'target': target.name, 'held': held.name,
                            'support': support, 'contact_bodies': contacts})
                        continue
                    accepted = candidate
                    verification_point=candidate_point
                    self._placement_record({'status': 'sampled', 'method': 'selected_surface_collision_geometry',
                        'candidate': candidate['index'], 'target': target.name, 'held': held.name,
                        'selected_point': point.tolist(), 'requested_yaw_degrees': yaw_degrees,
                        'verification_point':verification_point.tolist(),
                        'surface_kind':candidate.get('surface_kind','selected_surface'),
                        'selected_cavity':candidate.get('selected_cavity'),
                        'support_position': candidate['support_position'],
                        'settled_position': held.get_position_orientation()[0].tolist(),
                        'support': support, 'contact_bodies': contacts})
                    break
                if accepted is None:
                    raise SkillError('sampling_error', 'No collision-free, stable pose on the selected surface', changed=True)
            # Only the accepted physical result advances environment metrics,
            # object-state transition rules and the recorded replay.
            resting = RestingMotionWindow(self.og.sim.get_sim_step_dt())
            resting.add(*held.get_position_orientation())
            payload_resting = {id(child): RestingMotionWindow(self.og.sim.get_sim_step_dt())
                               for child, _, kind in dependencies if kind == 'OnTop'}
            for child, _, _ in dependencies:
                if id(child) in payload_resting:
                    payload_resting[id(child)].add(*child.get_position_orientation())
            self._settle_surface_contacts(held, target, payload, max_steps, resting=resting,
                                          payload_resting=payload_resting)
            adjacency = held.states[VerticalAdjacency].get_value()
            touching = any(path in self._placement_contacts(held,payload) for path in (link.prim_path for link in target.links.values()))
            official_on_top = bool(held.states[OnTop].get_value(target))
            supported, support = self._selected_surface_support(
                held, target, verification_point, touching, payload, resting=resting.report())
            self._placement_record({'status': 'postcondition_check', 'target': target.name, 'held': held.name,
                'position': held.get_position_orientation()[0].tolist(),
                'touching': touching, 'official_on_top': official_on_top, 'selected_surface_support': support,
                'verification_point':verification_point.tolist() if verification_point is not None else None,
                'target_below': target in adjacency.negative_neighbors,
                'target_above': target in adjacency.positive_neighbors,
                'grasp_released': self._get_held() is None})
            if not (supported if point is not None else official_on_top):
                raise SkillError('postcondition_error', 'Object is not stably supported by the selected surface', changed=True)
            self._verify_payload(dependencies, support_target=target,
                                 support_resting={key: window.report() for key, window in payload_resting.items()})
        return 'selected_surface_collision_support_after_free_settling' if point is not None else 'official_OnTop'

    def _selected_surface_support(self, held, target, point, touching, payload=(), *, require_contact=False, resting=None,
                                  require_neighborhood=True):
        """Check the actual lower collider patch after gravity, including lower shelves.

        No whole-box footprint, goal predicate mutation, or held-pose projection
        is used. The root/box center may lie outside a supported pan body.
        """
        from .surface_geometry import bottom_support_points, overlapping_surface_probes
        from omnigibson.utils.sampling_utils import raytest
        if point is None:
            return False, {'available': False, 'reason': 'No selected surface point'}
        probes = self.torch.as_tensor(bottom_support_points(self._collision_vertices(held)),
                                     dtype=point.dtype, device=point.device)
        anchor = probes[0]
        ignored = [link.prim_path for obj in (held, self.robot, *payload) for link in obj.links.values()]
        target_paths = {link.prim_path for link in target.links.values()}
        support_probes = []
        def proposals():
            for probe in probes:
                yield probe, 'bottom_vertex_or_anchor'
            # A stable contact may lie strictly inside a bottom face. Probe
            # actual face intersections when sparse vertices miss the support;
            # no bounding-box fill, wider tolerance or predicate override.
            if touching:
                for probe in overlapping_surface_probes(self._collision_triangles(held),
                        self._collision_triangles(target), float(point[2])):
                    yield point.new_tensor(probe), 'overlapping_collision_faces'
        for probe, source in proposals():
            start = probe + self.torch.tensor([0., 0., .03], device=point.device)
            end = probe - self.torch.tensor([0., 0., .04], device=point.device)
            hit = raytest(start, end, ignore_bodies=ignored)
            gap = float(probe[2] - hit['position'][2]) if hit['hit'] else None
            normal_z = float(hit['normal'][2]) if hit['hit'] else None
            height_error = abs(float(hit['position'][2] - point[2])) if hit['hit'] else None
            selected_xy_distance = float(self.torch.linalg.norm(probe[:2] - point[:2]))
            support_checks = {
                'support_ray_hits_selected_object': hit.get('rigidBody') in target_paths,
                'upward_support': normal_z is not None and normal_z >= .9,
                'bottom_near_support': gap is not None and -.01 <= gap <= .015,
                'selected_shelf_height': height_error is not None and height_error <= .05,
                # The supporting patch must be near the click. An object's
                # globally lowest vertex can be elsewhere (e.g. a tilted log
                # or a pan handle); it is not the selected contact witness.
                # NextTo instead bounds the final pose by its own relation.
                'selected_surface_neighborhood': not require_neighborhood or selected_xy_distance <= .20}
            support_probes.append({'position': probe.tolist(), 'source': source, 'checks': support_checks,
                                   'selected_xy_distance_m': selected_xy_distance,
                                   'support_hit_body': hit.get('rigidBody')})
            # Every geometric condition must hold at the SAME lower patch.
            # Keep looking when a support exists outside the selected area.
            # Stability and robot clearance remain independent requirements.
            if all(support_checks.values()):
                break
        contacts = self._placement_contacts(held, payload)
        selected_contact = bool(target_paths.intersection(contacts))
        # Sleeping bodies can omit their current contact manifold. Admit an
        # empty list only with the independent support patch and rest checks
        # below; a reported contact on another counter is still contradictory.
        robot_paths = {link.prim_path for link in self.robot.links.values()}
        speed = float(self.torch.linalg.norm(held.get_linear_velocity()))
        angular_speed = float(self.torch.linalg.norm(held.get_angular_velocity()))
        checks = {'released': self._get_held() is None,
                  **support_checks,
                  'selected_contact_consistent': selected_contact or (not require_contact and not contacts),
                  'settled': (bool(resting['stable']) if resting is not None
                              else speed <= .10 and angular_speed <= .5),
                  'not_supported_by_robot': not any(path in robot_paths for path in contacts)}
        contact = self._placement_contact_report(held, payload, allow_sleeping=True)
        checks['no_deep_penetration'] = contact['admissible']
        return all(checks.values()), {'checks': checks, 'touching_selected_object': touching,
            'selected_contact': selected_contact, 'contact_required': require_contact,
            'contact_geometry': contact,
            'speed_m_s': speed, 'angular_speed_rad_s': angular_speed, 'bottom_gap_m': gap,
            'normal_z': normal_z, 'selected_height_error_m': height_error,
            'selected_xy_distance_m': selected_xy_distance, 'bottom_anchor': anchor.tolist(),
            'support_probe': probe.tolist(),
            'bottom_anchor_xy_distance_m': float(self.torch.linalg.norm(anchor[:2] - point[:2])),
            'selected_neighborhood_required': require_neighborhood,
            'support_probes': support_probes,
            'resting_motion':resting,
            'support_hit_body': hit.get('rigidBody'), 'contact_bodies': contacts,
            'ignored_payload_names': [obj.name for obj in payload]}

    def _checked_place_inside(self, target, max_steps, point=None):
        from omnigibson.object_states import Inside
        held = self._get_held()
        if held is None:
            raise SkillError('empty_hand','No object is held')
        fillable = [link for link in target.links.values() if link.is_meta_link and
                    link.meta_link_type in {'fillable','openfillable'}]
        if not fillable or Inside not in held.states:
            raise SkillError('unsupported_relation','Target has no supported fillable volume')
        start=time.monotonic();before=self.sampling_physics_steps
        # Regrasping an item already inside the destination does not turn the
        # currently carried assembly into an existing resident to pin in place.
        moving_ids={id(held),*(id(child) for child,_ in getattr(self,'_carry_contents',[]))}
        residents=[record for record in self._container_payload(target) if id(record[0]) not in moving_ids]
        resident_count=len(residents)
        stabilized_payload=None
        with self._placement_context(target):
            contents=list(self._carry_contents) if self.ideal_carry else []
            particle_payload=getattr(self,'_placement_particle_payload',(None,[]))[1]
            # A single carrier still uses native Inside first. Its collision
            # geometry may fit even when its full AABB exceeds a conservative
            # annotated volume. Particle ownership and destination containment
            # are verified after settling; geometric fallback includes them.
            assembly_search=bool(contents)
            dependencies=list(getattr(self,'_carry_dependencies',[]))
            orientation=held.get_position_orientation()[1].clone()
            self._carry_detach()
            # Capture the anchored physics wrapper installed by the context.
            original_step=self.og.sim.step_physics
            native_sampling=False
            native_start=start;native_before=before
            def preserve_payload_poses():
                if residents:self._relocate_container_payload(residents)
            def preserve_residents(*args,**kwargs):
                # The near-bin arm can push an earlier item out while the
                # official sampler or the settling steps advance physics.
                # Existing contents are part of the destination's committed
                # state; keep their link-relative poses until this placement
                # has been checked. The new item remains free during settling.
                preserve_payload_poses()
                try:return original_step(*args,**kwargs)
                finally:
                    preserve_payload_poses()
            def bounded_step(*args,**kwargs):
                deadline=getattr(self,'deadline',None)
                if deadline is not None:deadline.check(changed=True)
                if native_sampling and (self.sampling_physics_steps-native_before>=1200 or time.monotonic()-native_start>30):
                    raise SkillError('native_sampling_budget_exhausted',
                                     'Native Inside search exhausted its reserved budget',changed=True)
                # Managed benchmark episodes have one execution deadline;
                # the legacy sampler caps apply only to standalone calls.
                if not getattr(deadline,'managed',False) and (
                        self.sampling_physics_steps-before>=min(6000,max_steps*4) or time.monotonic()-start>180):
                    raise SkillError('sampling_budget_exhausted','Volume sampler exceeded physics/time limit',changed=True)
                self.sampling_physics_steps+=1
                if not assembly_search and (contents or getattr(self,'_placement_particle_payload',(None,[]))[1]):
                    # Randomly flipping a plate is not a valid way to place
                    # its food (or a bag its particles). Preserve orientation.
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
            geometric_fallback=False
            open_native_state=None
            try:
                if assembly_search:
                    from .inside_sampling import sample_assembly_inside
                    sampled=sample_assembly_inside(self,held,target,contents,point=point,residents=residents)
                else:
                    from .inside_sampling import sample_assembly_inside
                    checkpoint=self.og.sim.dump_state(serialized=False)
                    identities=scene_object_identities(self.og.sim)
                    particles=snapshot_particle_state(self.og.sim)
                    def native():
                        nonlocal native_sampling,native_start,native_before,open_native_state
                        native_start=time.monotonic();native_before=self.sampling_physics_steps
                        native_sampling=True
                        try:
                            sampled=held.states[Inside].set_value(target,True)
                            if sampled and not self._inside_resting_check(held,target,contents,residents=residents):
                                return False
                            if sampled:
                                from .closure_clearance import check_closure_clearance
                                closure=check_closure_clearance(self,target,[held,*[obj for obj,_ in contents]])
                                if not closure['clear']:
                                    open_native_state=(self.og.sim.dump_state(serialized=False),
                                                       snapshot_particle_state(self.og.sim))
                                    self._placement_record({'status':'native_closed_articulation_collision',
                                        'held':held.name,'target':target.name,'closure':closure})
                                    return False
                            return sampled
                        finally:native_sampling=False
                    def restore():
                        if scene_object_identities(self.og.sim)!=identities or not particle_state_unchanged(particles):
                            raise SkillError('target_changed','Scene identities changed during Inside sampling',changed=True)
                        self.og.sim.load_state(checkpoint,serialized=False)
                        restore_particle_state(particles)
                    def geometric():
                        try:
                            fallback = (None if open_native_state is None else
                                {'state':open_native_state[0], 'particles':open_native_state[1],
                                 'candidate':{'candidate':0, 'origin':'native_sampler'}})
                            return sample_assembly_inside(self,held,target,contents,point=point,residents=residents,
                                                          open_fallback=fallback)
                        except SkillError as exc:
                            if exc.code!='sampling_budget_exhausted' or open_native_state is None:
                                raise
                            # A valid native Inside pose must not be lost just
                            # because the optional closure preference ran out
                            # of work. The caller restores that saved pose.
                            self.deadline.check(changed=True)
                            return False
                    if residents:
                        # Occupied containers need proposals on/around their
                        # existing contents. Do not first spend 30 seconds on a
                        # native search that ignores those support surfaces.
                        try:sampled=geometric()
                        except SkillError as exc:
                            if exc.code!='sampling_budget_exhausted':raise
                            sampled=False
                            self._placement_record({'status':'geometric_sampling_exhausted','error':str(exc)})
                        geometric_fallback=bool(sampled)
                        if not sampled:
                            restore()
                            try:sampled=native()
                            except SkillError as exc:
                                if exc.code!='native_sampling_budget_exhausted':raise
                                sampled=False
                                self._placement_record({'status':'native_sampling_exhausted','error':str(exc),
                                                        'preceded_by':'assembly_volume_search'})
                    else:
                        sampled,geometric_fallback=inside_sample_with_fallback(
                            native,geometric,restore,self._placement_record)
                if not sampled and open_native_state is not None:
                    if scene_object_identities(self.og.sim)!=identities or not particle_state_unchanged(particles):
                        raise SkillError('target_changed','Scene identities changed before native fallback',changed=True)
                    self.og.sim.load_state(open_native_state[0],serialized=False)
                    restore_particle_state(open_native_state[1])
                    sampled=True
                    geometric_fallback=False
                    self._placement_record({'status':'native_open_only_fallback',
                        'held':held.name,'target':target.name,
                        'note':'Inside is valid; closing may require rearranging contents'})
                sampling_done=True
            finally:
                if contents:RigidContactAPI.is_in_contact=original_contact
                self.og.sim.step_physics=preserve_residents if sampling_done else original_step
                self.frames_revision=-1
            try:
                if not sampled:
                    raise SkillError('sampling_error',('Assembly volume search could not find a valid placement'
                                     if assembly_search else 'Official volume sampler could not find a valid placement'),changed=True)
                # Containers must support the new object under gravity before
                # its resting pose becomes an ideal carry/stabilization pose.
                # Freezing an airborne sampler pose falsely validates Inside
                # and can leave food in a shelf or tools obstructing a lid.
                self._relocate_contents(held,contents)
                settling_steps=min(50,max_steps)
                local=getattr(self,'_local_operation',None)
                # Every accepted sampler pose has already passed free-release
                # resting checks. Optional closure search must not use up the
                # mandatory environment-step verification of that valid pose.
                commit=(local.verified_placement_commit(settling_steps) if local is not None
                        else nullcontext())
                with commit:
                    for _ in range(settling_steps):
                        # Environment.step does not dispatch through the patched
                        # sim.step_physics method used by the official sampler.
                        preserve_payload_poses()
                        try:self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
                        finally:preserve_payload_poses()
            finally:
                self.og.sim.step_physics=original_step
            if not held.states[Inside].get_value(target):
                raise SkillError('postcondition_error','Object did not remain inside after settling; try another placement',changed=True)
            self._verify_container_payload(target,residents)
            self._verify_payload(dependencies)
            if particle_payload:
                from .particle_payload import particles_in_volumes
                if not particles_in_volumes(self.env.scene,particle_payload,fillable):
                    raise SkillError('postcondition_error','Carried particles extend outside the destination volume',changed=True)
            if any(Inside not in obj.states or not obj.states[Inside].get_value(target) for obj,_ in contents):
                raise SkillError('postcondition_error','Carried contents do not fit inside the selected container',changed=True)
            if not getattr(target,'fixed_base',True):
                stabilized_payload=self._container_payload(target)
        if not getattr(target,'fixed_base',True):
            if not hasattr(self,'_stabilized_containers'):self._stabilized_containers={}
            self._stabilized_containers[target]=tuple(v.clone() for v in target.get_position_orientation())
            if not hasattr(self,'_stabilized_container_payloads'):self._stabilized_container_payloads={}
            self._stabilized_container_payloads[target]=stabilized_payload
        return {'primitive':'place_inside','implementation':('transactional_assembly_volume_with_official_Inside'
                if assembly_search else 'transactional_inside_geometry_first' if residents and geometric_fallback
                else 'transactional_Inside_with_geometric_fallback' if geometric_fallback
                else 'transactional_official_Inside_with_rigid_payload_sampling'),
                'postcondition':'Inside.get_value_after_settling','failure_policy':'restore_pre_action_state',
                'target_root_anchored':True,'existing_containment_verified':resident_count,
                'new_object_settling':'free_physics',
                'sampling_order':'geometry' if assembly_search else 'geometry_then_native' if residents else 'native_then_geometry',
                'sampling_physics_steps':self.sampling_physics_steps-before}

    def _inside_resting_check(self,held,target,contents,*,residents=()):
        """Validate release with the same anchoring cadence as an actual action.

        Raw samplers pin the target every physics substep. The real actuator
        pins once per control interval. A wall pushing an open lid can appear
        stable in the former and eject its contents in the latter.
        """
        from omnigibson.object_states import Inside
        from .resting_motion import RestingMotionWindow
        sim=self.og.sim;dt=sim.get_physics_dt();control_dt=sim.get_sim_step_dt()
        resting=RestingMotionWindow(control_dt)
        resting.add(*held.get_position_orientation())
        # Bypass the sampler's per-substep pose projection, not PhysX. All
        # controls, contacts and physics still run; these private trials do not
        # advance task metrics, render frames or trigger transition rules.
        physics_step=type(sim).step_physics.__get__(sim,type(sim))
        def anchors():
            if self._base_target is not None:self._restore_base_target()
            self._restore_object_anchor();self._restore_stabilized_containers()
            self._follow_placement_particles();self._relocate_container_payload(residents)
        for _ in range(max(1,int(.75/control_dt))):
            anchors();self._relocate_contents(held,contents)
            try:
                for _ in range(max(1,round(control_dt/dt))):
                    self.deadline.check(changed=True)
                    local=getattr(self,'_local_operation',None)
                    if local is not None:local.check()
                    self.sampling_physics_steps+=1;physics_step()
                sim.sync_physx_to_fabric()
            finally:anchors()
            resting.add(*held.get_position_orientation())
            if not held.states[Inside].get_value(target):break
            if resting.report()['stable']:
                if all(obj.states[Inside].get_value(target) for obj,_ in contents):
                    contact = self._placement_contact_report(held, [obj for obj,_ in contents], allow_sleeping=True)
                    if contact['admissible']:
                        return True
                    self._placement_record({'status':'inside_resting_contact_rejected',
                        'held':held.name,'target':target.name,'contact_geometry':contact})
                    return False
        self._placement_record({'status':'inside_unstable_candidate','held':held.name,'target':target.name,
                                'resting_motion':resting.report(),'anchoring_cadence_seconds':control_dt,
                                'speed_m_s':float(self.torch.linalg.norm(held.get_linear_velocity()))})
        return False

    def _inside_pose_overlap_ranker(self,held,assembly):
        from .placement_overlap import assembly_overlap_ranker
        return assembly_overlap_ranker(self,held,assembly)

    def _inside_overlap_ranker(self,assembly):
        """Cheap current-world envelope checks order, never reject, proposals."""
        import omnigibson.lazy as lazy
        query=lazy.omni.physx.get_physx_scene_query_interface()
        ignored={str(link.prim_path) for obj in [self.robot,*assembly] for link in obj.links.values()}
        ignored.update(str(link.prim_path) for obj in self.env.scene.objects for link in obj.links.values() if link.is_meta_link)
        cache={}
        def score(center,extent):
            key=tuple(round(float(v),5) for v in (*center,*extent))
            if key not in cache:
                collision=False
                def hit(row):
                    nonlocal collision
                    if str(row.rigid_body) in ignored:return True
                    collision=True;return False
                query.overlap_box(tuple(float(v)/2 for v in extent),tuple(map(float,center)),(0.,0.,0.,1.),hit,False)
                cache[key]=int(collision)
            return cache[key]
        return score

    def _inside_support_centers(self, held, target, volume, centers, center_offset, payload, check, point=None, residents=(),query_cache=None,bottom_offset=None):
        """Project assembly centers onto actual internal shelves and bottoms.

        All ray hits are needed: the closest hit alone can be the roof above
        a usable shelf. These are proposals, with the same collision, settling
        and containment checks as volume samples. No contact is waived.
        """
        from .surface_geometry import bottom_anchor
        from omnigibson.utils.sampling_utils import raytest
        position=held.get_position_orientation()[0]
        bottom=(self.torch.as_tensor(bottom_offset,dtype=position.dtype,device=position.device)
                if bottom_offset is not None else
                self.torch.as_tensor(bottom_anchor(self._collision_vertices(held)),
                                    dtype=position.dtype,device=position.device)-position)
        low,high=volume.visual_aabb
        ignored=[link.prim_path for obj in (held,self.robot,*payload) for link in obj.links.values()]
        target_paths={link.prim_path for link in target.links.values()}
        # Existing contents are valid supports too (e.g. a cookie on a sandwich).
        # Restrict this to verified residents of the selected container; the
        # proposal still needs collision, settling and official Inside checks.
        resident_paths={link.prim_path for obj,_,_ in residents for link in obj.links.values()
                        if not link.is_meta_link}
        support_paths=target_paths|resident_paths
        clearance=.025+.5*9.81*self.og.sim.get_physics_dt()**2
        proposals=[];surfaces=[]
        # Preserve the model's spatial hint. Three coarse volume fractions can
        # land on a central divider and two side walls while missing both usable
        # compartments. Project the selected point and its local neighbourhood
        # onto real supports before falling back to volume-wide positions.
        selected=[]
        if point is not None:
            offsets=[(0.,0.)]+[(x,y) for radius in (.04,.08,.12)
                              for x,y in ((radius,0.),(-radius,0.),(0.,radius),(0.,-radius))]
            for dx,dy in offsets:
                xy=(float(point[0])+dx+float(center_offset[0]-bottom[0]),
                    float(point[1])+dy+float(center_offset[1]-bottom[1]))
                if all(float(low[i])<=xy[i]<=float(high[i]) for i in (0,1)):selected.append(xy)
        # Midpoints of the volume's halves cover compartments on either side
        # of a divider without adding physics attempts or a larger work budget.
        split=[(float(low[0]+fx*(high[0]-low[0])),float(low[1]+fy*(high[1]-low[1])))
               for fx,fy in ((.25,.5),(.75,.5),(.25,.25),(.75,.25),
                             (.25,.75),(.75,.75),(.5,.25),(.5,.75))]
        # An unladen item can be rotated to fit the central cavity. Loaded
        # assemblies retain the selected compartment as their first proposal.
        central=[] if payload or getattr(self,'_placement_particle_payload',(None,[]))[1] else [
            (float((low[0]+high[0])/2),float((low[1]+high[1])/2))]
        for x,y in dict.fromkeys([*central,*selected,*split,*[(float(c[0]),float(c[1])) for c in centers]]):
            check()
            ax=x-float(center_offset[0])+float(bottom[0])
            ay=y-float(center_offset[1])+float(bottom[1])
            # Pose proposals only move ignored bodies. Reuse identical support
            # rays across inset/full domains and orientations in this one scan.
            key=(round(ax,6),round(ay,6),float(high[2]),float(low[2]))
            hits=None if query_cache is None else query_cache.get(key)
            if hits is None:
                hits=raytest([ax,ay,float(high[2])+.05],[ax,ay,float(low[2])-.05],
                             only_closest=False,ignore_bodies=ignored)
                if query_cache is not None:query_cache[key]=hits
            for hit in sorted(hits,key=lambda h:(abs(float(h['position'][2]-point[2]))
                                                 if point is not None else float(h['position'][2]))):
                if hit.get('rigidBody') not in support_paths or float(hit['normal'][2])<.9:continue
                z=float(hit['position'][2])+float(center_offset[2]-bottom[2])+clearance
                if not float(low[2])<=z-clearance<=float(high[2]):continue
                center=(x,y,z)
                if any(sum((a-b)**2 for a,b in zip(center,old))<1e-8 for old in proposals):continue
                proposals.append(center)
                surfaces.append({'center':center,'support_position':hit['position'].tolist(),
                                 'body':hit.get('rigidBody'),'collision':hit.get('collision')})
        self._placement_record({'status':'inside_support_proposals','held':held.name,'target':target.name,
            'volume':volume.prim_path,'clearance_m':clearance,'surfaces':surfaces})
        return proposals
