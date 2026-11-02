"""State placement with geometric proposals and native free settling."""
import json
import time

from ..contracts import SkillError


def scene_object_identities(sim):
    """State snapshots cannot recreate USD objects removed by transition rules."""
    scenes = getattr(sim, 'scenes', None)
    return None if scenes is None else tuple(
        (id(scene), frozenset(id(obj) for obj in scene.objects)) for scene in scenes)


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

    def _under_candidates(self, held, target, payload):
        """Measure supports below the selected parent without moving the scene."""
        from omnigibson.utils.sampling_utils import raytest
        position, orientation = held.get_position_orientation()
        assembly = [held, *payload]
        bottom = min(float(obj.aabb[0][2]) for obj in assembly)
        low, high = target.aabb
        ignored = [link.prim_path for obj in (*assembly, target, self.robot)
                   for link in obj.links.values()]
        supports = {link.prim_path: obj for obj in self.env.scene.objects
                    if obj not in (*assembly, target, self.robot)
                    for link in obj.links.values() if not link.is_meta_link}
        ranker = self._inside_pose_overlap_ranker(held, assembly)
        clearance = .002 + .5 * 9.81 * self.og.sim.get_physics_dt()**2
        candidates = []
        for index, fractions in enumerate(((.5,.5),(.25,.5),(.75,.5),(.5,.25),(.5,.75),
                                            (.25,.25),(.75,.25),(.25,.75),(.75,.75))):
            self.deadline.check()
            xy = low[:2] + (high[:2]-low[:2]) * low.new_tensor(fractions)
            start = low.new_tensor([float(xy[0]), float(xy[1]), float(high[2])+.3])
            end = start.clone(); end[2] = float(low[2])-1.5
            hit = raytest(start, end, ignore_bodies=ignored)
            support = supports.get(hit.get('rigidBody'))
            if not hit.get('hit') or float(hit['normal'][2]) < .9 or support is None:
                continue
            place = position.clone(); place[:2] = xy
            place[2] = hit['position'][2] + float(position[2])-bottom + clearance
            candidates.append({'index': index, 'pose': (place, orientation), 'support': support,
                'overlap_score': ranker(place.tolist(), orientation) if ranker is not None else 0})
        # Collider enclosures order proposals; they are not a rejection proof.
        return sorted(candidates, key=lambda candidate: candidate['overlap_score'])

    def _checked_place_under(self, target, max_steps):
        """Commit one measured support pose, then evaluate native free physics.

        Under.set_value performs nested rejection sampling and whole-scene
        restores. An action needs a release pose, not a new initial scene.
        """
        from omnigibson.object_states import OnTop, Under
        held = self._get_held()
        if held is None:
            raise SkillError('empty_hand', 'No object is held')
        if held is target or Under not in held.states:
            raise SkillError('unsupported_relation', 'Carried object cannot be placed under this target')
        contents = list(self._carry_contents)
        payload = [obj for obj, _ in contents]
        dependencies = list(getattr(self, '_carry_dependencies', []))
        candidates = self._under_candidates(held, target, payload)
        if not candidates:
            raise SkillError('sampling_error', 'No support surface found under the selected target')
        candidate = candidates[0]
        self._placement_record({'status': 'under_release', 'held': held.name, 'target': target.name,
            'candidate': candidate['index'], 'support': candidate['support'].name,
            'position': candidate['pose'][0].tolist(),
            'candidates': [{'index': row['index'], 'overlap_score': row['overlap_score']}
                           for row in candidates]})
        self._carry_detach()
        held.set_position_orientation(*candidate['pose']); held.keep_still()
        self._relocate_contents(held, contents)
        for _ in range(min(50, max_steps)):
            self._step(self.primitives._empty_action())
        contact = self._placement_contact_report(held, payload, allow_sleeping=True)
        checks = {'under': bool(held.states[Under].get_value(target)),
                  'supported': OnTop in held.states and bool(held.states[OnTop].get_value(candidate['support'])),
                  'no_deep_penetration': contact['admissible']}
        self._placement_record({'status': 'under_settled', 'held': held.name, 'target': target.name,
            'checks': checks, 'contact_geometry': contact})
        if not all(checks.values()):
            raise SkillError('postcondition_error', 'Released object did not remain supported under the selected target',
                             changed=True)
        self._verify_payload(dependencies)
        self.frames_revision = -1
        return {'primitive': 'place_under', 'implementation': 'measured_support_then_free_settling',
                'postcondition': 'Under.get_value_after_settling', 'failure_policy': 'retain_physical_result'}

    def _next_to_candidates(self, held, target, point, payload=(), yaw_degrees=None):
        """Find upward physical surfaces near the reference, without class filters."""
        import math
        from .surface_geometry import adjacent_support_points, bottom_anchor, rotated_surface_geometry
        from omnigibson.object_states import Touching, VerticalAdjacency
        from omnigibson.utils import transform_utils as T
        from omnigibson.utils.sampling_utils import raytest
        pose = held.get_position_orientation()
        relative,anchor = rotated_surface_geometry(self._collision_vertices(held),pose[0].cpu().numpy(),yaw_degrees)
        adjacent = adjacent_support_points(relative, anchor, self._collision_vertices(target), point.cpu().numpy())
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
        # A tray can overhang its table: rays through the clicked point and
        # bottom center then hit the floor. Prefer its actual supporting contact.
        # Negative adjacency also works on lower shelves with geometry above.
        reference_objects = {id(obj) for obj in target.states[VerticalAdjacency].get_value().negative_neighbors
                             if all(obj is not item for item in moving)
                             and target.states[Touching].get_value(obj)}
        if not reference_objects:
            # Static fixtures need not produce native contact pairs.
            reference_bottom = bottom_anchor(self._collision_vertices(target))
            reference_hits = [downward(point[:2], float(point[2])),
                              downward(reference_bottom[:2], float(reference_bottom[2]))]
            reference_objects = {id(by_link[hit['rigidBody']]) for hit in reference_hits
                                 if hit.get('hit') and float(hit['normal'][2]) >= .9
                                 and hit.get('rigidBody') in by_link}
        candidates = []
        proposals = [(self.torch.as_tensor(xy, dtype=point.dtype, device=point.device),
                      'separated_footprints') for xy in adjacent]
        for radius, angle in ((r, a) for r in (.12, .22, .34, .48)
                              for a in (0, 45, 90, 135, 180, 225, 270, 315)):
            theta = math.radians(angle)
            xy = point[:2] + self.torch.tensor([radius*math.cos(theta), radius*math.sin(theta)], device=point.device)
            proposals.append((xy, 'radial'))
        for index, (xy, method) in enumerate(proposals):
            delta = xy - point[:2]
            radius = float(self.torch.linalg.vector_norm(delta))
            angle = math.degrees(math.atan2(float(delta[1]), float(delta[0])))
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
                'clearance_m': clearance, 'proposal_method': method})
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
        """Choose by geometry, release once, and retain the physical result."""
        from omnigibson.object_states import NextTo
        held = self._get_held()
        if held is None:
            raise SkillError('empty_hand', 'No object is held')
        if held is target or NextTo not in held.states:
            raise SkillError('unsupported_relation', 'Carried object cannot be placed next to this target')
        contents = list(self._carry_contents)
        payload = [obj for obj, _ in contents]
        dependencies = list(getattr(self, '_carry_dependencies', []))
        candidates = self._next_to_candidates(held, target, point, payload, yaw_degrees)
        if not candidates:
            raise SkillError('placement_surface_unavailable', 'No support surface near the selected reference')
        supports = {id(row['support']): row['support'] for row in candidates}
        ranker = self._inside_pose_overlap_ranker(held, [held, *payload], support_objects=supports.values())
        candidate = candidates[0]
        best = float('inf')
        for row in candidates:
            score = ranker(*row['pose']) if ranker is not None else 0
            if score < best:
                candidate, best = row, score
            if score == 0:
                break
        support_point = self.torch.as_tensor(candidate['support_position'], dtype=point.dtype, device=point.device)
        self._placement_record({'status': 'next_to_release', 'target': target.name, 'held': held.name,
            'candidate': candidate['index'], 'support': candidate['support'].name,
            'proposal_overlap_score': best, 'position': candidate['pose'][0].tolist(),
            'requested_yaw_degrees': yaw_degrees})
        self._carry_detach()
        try:
            held.set_position_orientation(*candidate['pose'])
            self._relocate_contents(held, contents)
            held.keep_still(); held.wake()
            self._settle_surface_contacts(held, candidate['support'], payload, max_steps)
            valid, details = self._next_to_check(held, target, candidate['support'], payload,
                                                 support_point=support_point)
            self._placement_record({'status': 'next_to_commit', 'target': target.name,
                'held': held.name, 'valid': valid, **details})
            if not valid:
                checks = details['checks']
                stage = ('released_supported_not_adjacent' if checks['released'] and
                         checks['supported'] and not checks['next_to'] else None)
                raise SkillError('postcondition_error', 'NextTo did not remain after release',
                                 changed=True, stage=stage)
            self._verify_payload(dependencies, support_target=candidate['support'])
        finally:
            self.frames_revision = -1
            self._step(self.primitives._empty_action())
        return {'primitive': 'place_next_to', 'implementation': 'single_geometric_release_and_free_settling',
                'requested_yaw_degrees': yaw_degrees,
                'postcondition': 'official_NextTo_and_selected_surface_support_after_free_settling',
                'failure_policy': 'retain_physical_result'}

    def _attached_placement_bodies(self, root):
        if not hasattr(root,'scene'):return [root]
        from .carry import attachment_relation, support_closure
        candidates=[obj for obj in root.scene.objects if obj is not root]
        return [root,*[child for child,_,_ in support_closure(root,candidates,attachment_relation)]]

    def _collision_vertices(self, held):
        # Rigid attachments form one moving structure; a tabletop's lowest
        # collider is at its feet, while loose contents remain payload checks.
        import numpy as np
        # Root poses come from PhysX; mesh.scaled_transform reads Fabric.
        # Render-free stepping can leave Fabric at an older pose. Refresh
        # this geometry read without rendering, stepping, or moving bodies.
        self.og.sim.sync_physx_to_fabric()
        return np.concatenate([self._object_collision_vertices(obj)
                               for obj in self._attached_placement_bodies(held)])

    def _object_collision_vertices(self, held):
        """Read active physical geometry, including world scale, without a box proxy."""
        import numpy as np
        from omnigibson.utils.constants import PrimType
        if held.prim_type == PrimType.CLOTH:
            return held.root_link.compute_particle_positions().detach().cpu().numpy()
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

    def _surface_candidates(self, held, target, point, yaw_degrees=None, payload=(), *, offsets=None,
                            roll_degrees=None, spread_surface=False, center_footprint=False):
        """Propose root poses at the selected collision surface, with the actual shape.

        Surface rays ignore the moving assembly at its old pose. Rigid objects
        use their authored resting tilt so a small held tilt does not align a
        low corner instead of the supporting base. Cloth retains its deformed
        footprint. Candidate ranking is geometric; release and settling happen once.
        """
        import math
        from .surface_geometry import rotated_surface_geometry
        from omnigibson.utils.sampling_utils import raytest
        from omnigibson.utils import transform_utils as T
        position, orientation = held.get_position_orientation()
        origin = position.detach().cpu().numpy()
        geometry = self._collision_vertices(held)
        orientations = getattr(held, 'orientations', None)
        if orientations is not None and not center_footprint:
            from .surface_geometry import native_resting_orientation
            quaternion, rotation = native_resting_orientation(
                orientation.detach().cpu().numpy(), orientations)
            geometry = (geometry-origin) @ rotation.T + origin
            orientation = self.torch.as_tensor(quaternion, dtype=orientation.dtype,
                                                device=orientation.device)
        vertices, anchor = rotated_surface_geometry(geometry, origin, yaw_degrees, roll_degrees,
                                                   center_footprint=center_footprint)
        if roll_degrees is not None:
            rotation = T.euler2quat(self.torch.tensor([math.radians(roll_degrees), 0., 0.],
                                                      device=orientation.device))
            orientation = T.quat_multiply(rotation, orientation)
        if yaw_degrees is not None:
            rotation = T.euler2quat(self.torch.tensor([0., 0., math.radians(yaw_degrees)],
                                                      device=orientation.device))
            orientation = T.quat_multiply(rotation, orientation)
        ignored = [link.prim_path for obj in (held, self.robot, *payload) for link in obj.links.values()]
        target_paths = {link.prim_path for link in target.links.values()}
        if offsets is None:
            offsets = [(0., 0.)] + [(x, y) for radius in (.04, .08, .12)
                                   for x, y in ((radius, 0), (-radius, 0), (0, radius), (0, -radius))]
        if spread_surface:
            # A batch can fill the click's neighborhood while the rest of the
            # same tray remains empty. Propose its other upward surface patches,
            # retaining the selected height so a different shelf is not chosen.
            from .surface_geometry import upward_surface_points
            offsets = [*offsets, *[(float(p[0]-point[0]), float(p[1]-point[1]))
                for p in upward_surface_points(self._collision_triangles(target))
                if abs(float(p[2]-point[2])) <= .05]]
        candidates = []
        # Release immediately above the measured collision surface. A 2.5 cm
        # drop (the native rejection sampler's clearance) unnecessarily jolts
        # loaded vessels and spills contents. Contacts are admitted by their
        # actual penetration below, so we need only a small geometric gap plus
        # the first free physics step's gravity displacement. Free settling and
        # all support/payload postconditions still apply.
        clearance = .002 + .5 * 9.81 * self.og.sim.get_physics_dt() ** 2
        for index, (x, y) in enumerate(offsets):
            center = point + self.torch.tensor([x, y, 0.], device=point.device)
            end = center - self.torch.tensor([0., 0., .08], device=point.device)
            attempts = []
            # Start close to the click: a ray from above the whole object can
            # hit the shelf ABOVE the selected lower shelf. Taller starts only
            # recover small visual/collision-surface offsets on this shelf.
            # PhysX returns distance zero for a ray originating inside a
            # collider; that hit is the origin, not a support surface.
            for height in (.03, .10, .20):
                start = center + self.torch.tensor([0., 0., height], device=point.device)
                hit = raytest(start, end, ignore_bodies=ignored)
                reason = ('no_support_surface' if not hit['hit'] else
                          'ray_origin_inside_collision' if float(hit['distance']) == 0. else
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
            'yaw_degrees': yaw_degrees, 'roll_degrees': roll_degrees, 'collision_vertex_count': len(vertices),
            'rotated_geometry_extent': (vertices.max(axis=0)-vertices.min(axis=0)).tolist(),
            'bottom_anchor_offset': anchor.tolist(), 'candidate_count': len(candidates)})
        rim_candidates = self._open_rim_candidates(held,target,point,vertices,anchor,orientation,
                          offsets,payload,clearance,start_index=len(offsets))
        # A rim click can balance an object's center just outside the box.
        # Try nearby poses with the lower patch centered inside that same
        # open cavity first; each still needs actual collision and settling.
        if any(c.get('selected_rim_surface') for c in rim_candidates):
            return rim_candidates + candidates
        if not candidates and not rim_candidates:
            # A side click on a single-level support still identifies its
            # receiving surface. Only project when actual collision triangles
            # have one upward plane; multi-shelf objects retain click height.
            from .surface_geometry import single_support_height
            height = single_support_height(self._collision_triangles(target))
            if height is not None and abs(height-float(point[2])) > .05:
                projected = point.clone()
                projected[2] = height
                candidates = self._surface_candidates(held, target, projected,
                    yaw_degrees, payload, offsets=offsets, roll_degrees=roll_degrees,
                    center_footprint=center_footprint)
                for candidate in candidates:
                    candidate.update(verification_point=projected, surface_kind='single_support_plane')
        return candidates + rim_candidates

    def _surface_fallback_candidates(self, held, target, point, yaw_degrees=None, payload=(), *,
                                     roll_degrees=None):
        """Cover nearby diagonal positions and yaw when the local geometry is crowded.

        The selected object, shelf and 20 cm verification neighborhood stay
        unchanged. Vessels retain their authored resting tilt; an explicit yaw
        remains binding. This helper only constructs geometry.
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
            for candidate in self._surface_candidates(held, target, point, angle, payload,
                                                       offsets=offsets, roll_degrees=roll_degrees):
                position, orientation = candidate['pose']
                key = tuple(round(float(v), 5) for v in (*position, *orientation))
                if key in seen:
                    continue
                seen.add(key)
                candidate.update(index=len(proposals), proposal_yaw_degrees=angle)
                proposals.append(candidate)
        proposals.sort(key=lambda c: (round(sum(v*v for v in c['offset_xy_m']), 6),
                                      abs(c['proposal_yaw_degrees'] or 0.)))
        self._placement_record({'status': 'surface_proposal_coverage', 'target': target.name, 'held': held.name,
            'proposals': len(proposals), 'angles_degrees': angles, 'ordering': 'distance_then_yaw'})
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
            floor_distance = abs(float(point[2]-low[2]))
            rim_distance = abs(float(point[2]-high[2]))
            # Shallow vessels can put both levels within tolerance. Preserve
            # inward rim proposals when the click is closer to the rim.
            floor_selected = floor_distance <= .05 and floor_distance <= rim_distance
            rim_selected = rim_distance <= .05
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

    def _select_surface_candidate(self, held, target, point, yaw_degrees, payload, *, roll_degrees=None):
        """Choose geometry without moving, stepping, or snapshotting the scene."""
        if point is None:
            raise SkillError('placement_surface_unavailable', 'Select a receiving surface for placement')
        candidates = self._surface_candidates(held, target, point, yaw_degrees, payload,
                                              roll_degrees=roll_degrees)
        ranker = self._inside_pose_overlap_ranker(held, [held, *payload], support_objects=(target,))

        def score(candidate):
            position, orientation = candidate['pose']
            value = ranker(position.tolist(), orientation) if ranker is not None else 0.
            candidate['proposal_overlap_score'] = value
            return value

        # Prefer the clicked neighborhood; expand geometry only if it is
        # occupied. Envelope overlap ranks proposals, never certifies failure.
        candidate = min(candidates, key=score, default=None)
        if candidate is None or candidate['proposal_overlap_score']:
            alternatives = self._surface_fallback_candidates(
                held, target, point, yaw_degrees, payload, roll_degrees=roll_degrees)
            candidate = min([*candidates, *alternatives], key=score, default=None)
        if candidate is None:
            raise SkillError('placement_surface_unavailable', 'No upward receiving surface near the selected point')
        return candidate

    def _checked_place_on_top(self, target, max_steps, point=None, yaw_degrees=None, *, roll_degrees=None):
        held = self._get_held()
        if held is None:
            raise SkillError('empty_hand', 'No object is held')
        try:
            check = self._try_place_on_top(held, target, max_steps, point, yaw_degrees,
                                           roll_degrees=roll_degrees)
        finally:
            self.frames_revision = -1
        return {'primitive': 'place_on_top', 'implementation': 'single_geometric_release_and_free_settling',
                'postcondition': check, 'failure_policy': 'retain_physical_result'}

    def _placement_contacts(self, held, payload=()):
        from .placement_contacts import assembly_contact_pairs
        assembly = (held, *payload)
        paths = {link.prim_path for obj in assembly for link in obj.links.values()}
        pairs = assembly_contact_pairs(held, payload)
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
        """Let the released assembly settle before reading native state transitions.

        Sampling can put a small object to sleep before its contact state has
        been read. PhysX then omits current contacts, while OG's kinematic
        cache can retain a false Touching/OnTop value at the unchanged pose.
        Wake the released assembly during the final five intervals; gravity
        and contacts still determine every pose and predicate. Keep the same
        physics duration, but defer Environment.step until its support has been read:
        slicing can legitimately replace a released object on this surface.
        """
        objects = (held, target, *payload)
        steps = min(50, max_steps)
        substeps = max(1, round(self.og.sim.get_sim_step_dt() / self.og.sim.get_physics_dt()))
        for index in range(steps):
            if index == max(0, steps - 5):
                for obj in (held, *payload):
                    obj.wake()
                for obj in objects:
                    for state in obj.states.values():
                        state.clear_cache()
            for _ in range(substeps):
                self._placement_physics_step()
            self.og.sim.sync_physx_to_fabric()
            if resting is not None:
                resting.add(*held.get_position_orientation())
            for child in payload:
                window=(payload_resting or {}).get(id(child))
                if window is not None:window.add(*child.get_position_orientation())
        for obj in objects:
            for state in obj.states.values():
                state.clear_cache()

    def _try_place_on_top(self, held, target, max_steps, point, yaw_degrees=None, *, roll_degrees=None):
        """One geometric placement and one physical outcome, without rollback."""
        from omnigibson.object_states import OnTop
        from .resting_motion import RestingMotionWindow
        contents = list(self._carry_contents)
        payload = [obj for obj, _ in contents]
        dependencies = list(getattr(self, '_carry_dependencies', []))
        candidate = self._select_surface_candidate(held, target, point, yaw_degrees, payload,
                                                   roll_degrees=roll_degrees)
        verification_point = candidate.get('verification_point', point)
        self._placement_record({'status': 'surface_release', 'target': target.name, 'held': held.name,
            'candidate': candidate['index'], 'position': candidate['pose'][0].tolist(),
            'selected_point': point.tolist(), 'verification_point': verification_point.tolist(),
            'requested_yaw_degrees': yaw_degrees,
            'proposal_yaw_degrees': candidate.get('proposal_yaw_degrees', yaw_degrees),
            'proposal_overlap_score': candidate.get('proposal_overlap_score'),
            'surface_kind': candidate.get('surface_kind', 'selected_surface')})
        self._carry_detach()
        held.set_position_orientation(*candidate['pose'])
        self._relocate_contents(held, contents)
        held.keep_still()
        held.wake()
        resting = RestingMotionWindow(self.og.sim.get_sim_step_dt())
        payload_resting = {id(child): RestingMotionWindow(self.og.sim.get_sim_step_dt())
                           for child, _, kind in dependencies if kind == 'OnTop'}
        self._settle_surface_contacts(held, target, payload, max_steps, resting=resting,
                                      payload_resting=payload_resting)
        try:
            contacts = self._placement_contacts(held, payload)
            touching = any(link.prim_path in contacts for link in target.links.values())
            supported, support = self._selected_surface_support(
                held, target, verification_point, touching, payload, resting=resting.report())
            # Residual motion is diagnostic; losing the requested support is
            # a failed result. Never retry or rewind either outcome here.
            supported = all(value for key, value in support['checks'].items() if key != 'settled')
            self._placement_record({'status': 'postcondition_check', 'target': target.name, 'held': held.name,
                'position': held.get_position_orientation()[0].tolist(),
                'official_on_top': bool(held.states[OnTop].get_value(target)),
                'selected_surface_support': support, 'supported': supported,
                'verification_point': verification_point.tolist(), 'grasp_released': self._get_held() is None})
            if not supported:
                raise SkillError('postcondition_error', 'Object did not settle on the selected surface', changed=True)
            self._verify_payload(dependencies, support_target=target,
                                 support_resting={key: window.report() for key, window in payload_resting.items()})
        finally:
            # The release is real even when its result fails. Advance native
            # transitions and evaluation on both paths, without rewinding.
            # Slicing may replace the object here; do not access it afterward.
            self._step(self.primitives._empty_action())
        return 'selected_surface_collision_support_after_free_settling'

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
        """Release once into the selected volume and report the physical result."""
        from omnigibson.object_states import Inside
        from .inside_sampling import select_inside_release
        held = self._get_held()
        if held is None:
            raise SkillError('empty_hand', 'No object is held')
        if Inside not in held.states or not any(link.is_meta_link and
                link.meta_link_type in {'fillable', 'openfillable'} for link in target.links.values()):
            raise SkillError('unsupported_relation', 'Target has no supported fillable volume')
        contents = list(self._carry_contents)
        dependencies = list(self._carry_dependencies)
        moving_ids = {id(held), *[id(child) for child, _ in contents]}
        residents = [row for row in self._container_payload(target) if id(row[0]) not in moving_ids]
        candidate = select_inside_release(self, held, target, contents, point=point, residents=residents)
        before = self.sampling_physics_steps
        self._placement_record({'status': 'inside_release', 'held': held.name, 'target': target.name,
            **{k: v for k, v in candidate.items() if k != 'pose'},
            'position': candidate['pose'][0].tolist(), 'orientation': candidate['pose'][1].tolist()})
        self._carry_detach()
        held.set_position_orientation(*candidate['pose'])
        self._relocate_contents(held, contents)
        held.keep_still(); held.wake()
        self._settle_surface_contacts(held, target, [obj for obj, _ in contents], max_steps)
        try:
            inside = bool(held.states[Inside].get_value(target))
            self._placement_record({'status': 'inside_result', 'held': held.name, 'target': target.name,
                'official_inside': inside, 'position': held.get_position_orientation()[0].tolist()})
            if not inside:
                raise SkillError('postcondition_error', 'Object did not remain inside after release', changed=True)
            try:
                self._verify_container_payload(target, residents)
                self._verify_payload(dependencies)
            except SkillError as exc:
                if exc.code == 'postcondition_error':
                    exc.stage = 'released_inside_payload_unconfirmed'
                raise
        finally:
            self.frames_revision = -1
            # This is the real outcome, including failure. Native transitions
            # and evaluation advance once; no world state is restored.
            self._step(self.primitives._empty_action())
        return {'primitive': 'place_inside', 'implementation': 'single_geometric_release_and_free_settling',
                'postcondition': 'Inside_and_retained_payload_after_free_settling',
                'failure_policy': 'retain_physical_result',
                'sampling_physics_steps': self.sampling_physics_steps - before}

    def _inside_resting_check(self,held,target,contents,*,residents=()):
        """Observe native free settling without restoring any body pose."""
        from omnigibson.object_states import Inside
        from .resting_motion import RestingMotionWindow
        sim=self.og.sim;dt=sim.get_physics_dt();control_dt=sim.get_sim_step_dt()
        resting=RestingMotionWindow(control_dt)
        resting.add(*held.get_position_orientation())
        # Use native PhysX directly and account for each trial step below.
        # Controls, contacts and physics still run; these private trials do not
        # advance task metrics, render frames or trigger transition rules.
        physics_step=type(sim).step_physics.__get__(sim,type(sim))
        for _ in range(max(1,int(.75/control_dt))):
            for _ in range(max(1,round(control_dt/dt))):
                self.deadline.check(changed=True)
                local=getattr(self,'_local_operation',None)
                if local is not None:local.check()
                self.sampling_physics_steps+=1;physics_step()
            sim.sync_physx_to_fabric()
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
                                'resting_motion':resting.report(),'observation_interval_seconds':control_dt,
                                'speed_m_s':float(self.torch.linalg.norm(held.get_linear_velocity()))})
        return False

    def _inside_pose_overlap_ranker(self,held,assembly,*,support_objects=()):
        from .placement_overlap import assembly_overlap_ranker
        return assembly_overlap_ranker(self,held,assembly,support_objects=support_objects)

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
        allow_drop=not payload and not getattr(self,'_placement_particle_payload',(None,[]))[1]
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
                # An unladen item can start above existing contents and fall
                # into the cavity. Its support must be inside; its center is
                # checked against native Inside only after free settling.
                admission_z=float(hit['position'][2]) if allow_drop else z-clearance
                if not float(low[2])<=admission_z<=float(high[2]):continue
                center=(x,y,z)
                if any(sum((a-b)**2 for a,b in zip(center,old))<1e-8 for old in proposals):continue
                proposals.append(center)
                surfaces.append({'center':center,'support_position':hit['position'].tolist(),
                                 'body':hit.get('rigidBody'),'collision':hit.get('collision')})
        self._placement_record({'status':'inside_support_proposals','held':held.name,'target':target.name,
            'volume':volume.prim_path,'clearance_m':clearance,'surfaces':surfaces})
        return proposals
