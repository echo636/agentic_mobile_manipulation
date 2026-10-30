"""Transfer contents between the held vessel and an RGB-selected object.

Pour loose rigid objects and particles, or dispense from an authored source.
No task bindings, recipes, goal predicates, or material substitutions are used.
"""
import math
import time

from ..contracts import SkillError
from .particle_payload import snapshot_particles, _indices
from .particle_view import physical_particle_view


def free_volume_positions(scene, destination, system, limit, *, diagnostics=None):
    """Find a bounded portion with progressively finer collision-checked grids.

    Particle rest spacing constrains accepted positions, not search resolution:
    a single lattice can skip an entire free layer in a shallow, partly filled
    vessel. Refine only when the preceding pass found no usable position.
    """
    import torch
    from omnigibson.object_states import ContainedParticles
    link = destination.states[ContainedParticles].link
    low, high = link.visual_aabb
    radius = float(system.particle_radius)
    spacing = float(system.particle_particle_rest_distance)
    if not all(math.isfinite(x) and x > 0 for x in (radius, spacing)):
        raise SkillError('execution_error', 'Invalid physical particle dimensions')
    report = diagnostics if diagnostics is not None else {}
    report.update(bounds_low=low.tolist(), bounds_high=high.tolist(),
                  radius_m=radius, rest_spacing_m=spacing, limit=limit, passes=[])
    empty = lambda: low.new_empty((0, 3))
    if limit <= 0:
        report['termination'] = 'no_source_material'
        return empty()
    if bool(((high - low) <= 2 * radius).any()):
        report['termination'] = 'sphere_diameter_exceeds_axis'
        return empty()
    occupied = []
    for other in scene.active_systems.values():
        if other.n_particles and physical_particle_view(other) is not None:
            centers = other.get_particles_position_orientation()[0]
            reach = radius + float(other.particle_radius)
            near = ((centers >= low - reach) & (centers <= high + reach)).all(dim=1)
            if bool(near.any()):
                occupied.append((centers[near], reach))
    accepted = []
    deadline = time.monotonic() + 15.0
    report['termination'] = 'candidate_grids_exhausted'
    for refinement in (1., .5, .25):
        axes = [torch.arange(float(a) + radius, float(b) - radius, spacing * refinement,
                             dtype=low.dtype, device=low.device) for a, b in zip(low, high)]
        phase = dict(search_spacing_m=spacing * refinement, axis_counts=[len(a) for a in axes],
                     in_volume=0, particle_clear=0, rigid_checks=0, rigid_clear=0)
        report['passes'].append(phase)
        if any(len(axis) == 0 for axis in axes):
            report['termination'] = 'sphere_diameter_exceeds_axis'
            return empty()
        xy = torch.cartesian_prod(axes[0], axes[1]).reshape(-1, 2)
        xy = xy[((xy - (low[:2] + high[:2]) / 2)**2).sum(dim=1).argsort()]
        for z in axes[2]:
            if time.monotonic() >= deadline:
                report['termination'] = 'sampling_budget_exhausted'
                break
            points = torch.cat((xy, z.expand(len(xy), 1)), dim=1)
            points = points[link.check_points_in_volume(points)]
            phase['in_volume'] += len(points)
            for start in range(0, len(points), 256):
                batch = points[start:start + 256]
                clear = torch.ones(len(batch), dtype=torch.bool, device=batch.device)
                for centers, reach in occupied:
                    for offset in range(0, len(centers), 512):
                        clear &= (torch.cdist(batch, centers[offset:offset + 512]) >= reach).all(dim=1)
                batch = batch[clear]
                phase['particle_clear'] += len(batch)
                for position in batch:
                    if time.monotonic() >= deadline:
                        report['termination'] = 'sampling_budget_exhausted'
                        return torch.stack(accepted) if accepted else empty()
                    # Fine search grids must not create overlapping new particles.
                    if accepted and bool(((torch.stack(accepted) - position).norm(dim=1) < spacing - 1e-7).any()):
                        continue
                    phase['rigid_checks'] += 1
                    if not bool(system.check_in_contact(position.unsqueeze(0))[0]):
                        phase['rigid_clear'] += 1
                        accepted.append(position)
                        if len(accepted) == limit:
                            report['termination'] = 'portion_limit'
                            return torch.stack(accepted)
        if accepted:
            report['termination'] = 'partial_portion'
            return torch.stack(accepted)
        if time.monotonic() >= deadline:
            break
    return empty()


def move_particle_centers(system, indices, positions, orientations=None):
    """Translate selected existing particles without changing count or identity."""
    import torch
    view = physical_particle_view(system)
    transforms = view.get_transforms().clone()
    centers = system.get_particles_position_orientation()[0]
    if orientations is None:
        transforms[indices, :3] += positions - centers[indices]
    else:
        from omnigibson.utils import transform_utils as T
        transforms[indices, :3] = positions - T.quat2mat(orientations) @ system._particle_offset
        transforms[indices, 3:] = orientations
    velocities = view.get_velocities().clone()
    velocities[indices] = 0
    all_indices = torch.arange(len(transforms), device=transforms.device)
    view.set_transforms(transforms, indices=all_indices)
    view.set_velocities(velocities, indices=all_indices)


def free_surface_positions(scene, destination, system, limit, *, diagnostics):
    """Use native top-down surface sampling without generating new material."""
    import torch
    from omnigibson.utils.sampling_utils import sample_cuboid_on_object_full_grid_topdown

    radius = float(system.particle_radius)
    low, high = destination.aabb
    # Keep large supports from producing an unbounded ray grid for one portion.
    spacing = max(float(system.particle_particle_rest_distance),
                  math.sqrt(float((high[:2] - low[:2]).prod()) / max(1, limit * 4)))
    samples = sample_cuboid_on_object_full_grid_topdown(
        destination, ray_spacing=spacing, cuboid_dimensions=torch.zeros(3),
        aabb_offset=torch.tensor([-radius, -radius, radius]),
        cuboid_bottom_padding=radius + .001, undo_cuboid_bottom_padding=False)
    positions = [sample[0] for sample in samples if sample[0] is not None]
    diagnostics.update(method='native_topdown_surface', candidates=len(positions),
                       sampling_distance_m=spacing)
    if not positions:
        return low.new_empty((0, 3))
    positions = torch.stack(positions)
    # Native rays establish support. Reject occupied space before moving the
    # existing portion, including PBD particles absent from rigid scene queries.
    clear = ~system.check_in_contact(positions)
    for other in scene.active_systems.values():
        if not other.n_particles or physical_particle_view(other) is None:
            continue
        centers = other.get_particles_position_orientation()[0]
        reach = radius + float(other.particle_radius)
        near = ((centers >= positions.amin(0) - reach) &
                (centers <= positions.amax(0) + reach)).all(dim=1)
        for chunk in centers[near].split(512):
            clear &= (torch.cdist(positions, chunk) >= reach).all(dim=1)
    positions = positions[clear]
    return positions[torch.randperm(len(positions))[:limit]]


class CheckedMaterialTransfer:
    def _pour_objects(self, source):
        """Pour loose stacked items separately; nested vessels keep contents."""
        from omnigibson.object_states import ContainedParticles, Inside, OnTop, AttachedTo
        dependencies = self._grasp_dependencies(source)
        members = [child for child, _, _ in dependencies]
        candidates = [child for child, _, kind in dependencies if kind in {'Inside', 'OnTop'}]
        return [child for child in candidates if not any(
            other is not child and (
                AttachedTo in child.states and child.states[AttachedTo].get_value(other)
                or ContainedParticles in other.states and any(
                    state in child.states and child.states[state].get_value(other)
                    for state in (Inside, OnTop)))
            for other in members)]

    def _pour_rigid_objects(self, source, destination, objects, max_steps):
        """Set containment or a measured support pose, then leave free physics.

        Stop when the receiver cannot accept the next object. Earlier transfers
        remain real progress; never roll back the scene or reconstruct food.
        """
        from omnigibson.object_states import ContainedParticles, Inside, OnTop
        from omnigibson.utils import transform_utils as T
        from .particle_payload import relocate_particles
        relation = Inside if ContainedParticles in destination.states else OnTop
        moved = []
        for obj in objects:
            self.deadline.check(changed=bool(moved))
            # Loose peers have their own transfer in this batch. Only carry
            # this object's nested contents or attached assembly with it.
            dependencies = [(child, parent, kind) for child, parent, kind in self._grasp_dependencies(obj)
                            if child not in objects]
            contents = [(child, T.relative_pose_transform(
                *child.get_position_orientation(), *obj.get_position_orientation()))
                for child, _, _ in dependencies]
            particles = snapshot_particles(self.env.scene, [obj, *[child for child, _ in contents]],
                                           include_supported=True)
            if relation not in obj.states:
                break
            if relation is OnTop:
                # Native cuboid sampling rejects an entire footprint when
                # existing loose material covers part of the receiver. Use
                # the same exposed collision surface as individual placement.
                # Commit one pose, with no scene rewind or contents pinning.
                low, high = destination.aabb
                point = (low + high) / 2
                point[2] = high[2]
                candidates = self._surface_candidates(obj, destination, point)
                if not candidates:
                    break
                obj.set_position_orientation(*candidates[0]['pose'])
                obj.keep_still()
            elif not obj.states[relation].set_value(destination, True):
                break
            self._relocate_contents(obj, contents)
            relocate_particles(self.env.scene, particles)
            moved.append(obj)
            if relation is OnTop:
                for _ in range(max(1, round(.5 / self.og.sim.get_physics_dt()))):
                    self._placement_physics_step()
                retained = bool(obj.states[OnTop].get_value(destination))
                self._placement_record({'status': 'rigid_surface_pour_settled',
                    'object': obj.name, 'destination': destination.name,
                    'candidate': candidates[0]['index'], 'official_on_top': retained})
                if not retained:
                    break
        if not moved:
            raise SkillError('sampling_error', 'The receiver could not accept a loose object', changed=True)
        for _ in range(min(6, max_steps)):
            self._step(self.primitives._empty_action())
        return moved

    def _checked_transfer(self, primitive, target, max_steps):
        from omnigibson.object_states import ContainedParticles, Inside, OnTop

        source, destination = self._transfer_preconditions(primitive, target)
        objects = self._pour_objects(source) if primitive == 'pour' else []
        moved = self._pour_rigid_objects(source, destination, objects, max_steps) if objects else []
        result = self._transfer_material_portion(primitive, source, destination, max_steps,
                                                 empty_ok=bool(moved))
        if moved:
            # Material transfer can advance physics again. Count rigid contents
            # after that settling, so the receipt describes the returned scene.
            # Native slicing/cooking may replace an object during those steps;
            # its old contact-state handle is no longer valid.
            present = [obj for obj in moved
                       if self.env.scene.object_registry('name', obj.name) is obj]
            changed = len(present) != len(moved)
            relation = Inside if ContainedParticles in destination.states else OnTop
            retained = sum(bool(obj.states[relation].get_value(destination)) for obj in present)
            rigid = {'moved_objects': len(moved), 'retained_objects': retained,
                     'identities_changed_during_settling': changed,
                     'source_remaining_objects': len(self._pour_objects(source))}
            self._placement_record({'status': 'rigid_contents_poured', 'source': source.name,
                'destination': destination.name, 'objects': [obj.name for obj in moved], **rigid})
            if not retained and not changed:
                raise SkillError('postcondition_error', 'Poured objects did not remain at the receiver', changed=True)
            result.update(rigid_transfer=rigid, implementation='native_rigid_and_particle_transfer')
        return result

    def _transfer_preconditions(self, primitive, target):
        from omnigibson.object_states import ContainedParticles, Inside, Open
        held = self._get_held()
        if held is None:
            raise SkillError('empty_hand', 'Carry a material source or receiving vessel first')
        source, destination = (held, target) if primitive == 'pour' else (target, held)
        if source is destination:
            raise SkillError('same_transfer_vessel', 'Material source and destination are the same vessel')
        if primitive == 'scoop' and ContainedParticles not in destination.states:
            raise SkillError('invalid_transfer_destination',
                'Scoop needs a held receiving vessel')
        for obj in (source, destination):
            if Open in obj.states and not obj.states[Open].get_value():
                raise SkillError('container_closed', 'Open the material source and receiving vessel before transfer')
            if Inside in obj.states:
                for parent in self.env.scene.objects:
                    if (parent not in (source, destination) and Open in getattr(parent, 'states', {})
                            and not parent.states[Open].get_value() and obj.states[Inside].get_value(parent)):
                        raise SkillError('container_closed', 'Selected material vessel is inside a closed container')
        return source, destination

    def _ready_dispenser(self, source, *, tilt):
        """Respect authored material, switch, saturation and global dose limits.

        Ideal pouring supplies the local tilt/contact, just as ideal grasp
        supplies local hand motion. Conditions are evaluated at the outlet's
        downward orientation, then the source pose is restored before physics.
        """
        from omnigibson.object_states import ParticleSource, ParticleApplier
        from omnigibson.utils import transform_utils as T
        modifier = source.states.get(ParticleSource, source.states.get(ParticleApplier))
        if modifier is None:
            return None, []
        pose = tuple(value.clone() for value in source.get_position_orientation())
        try:
            if tilt:
                outlet_orientation = modifier.link.get_position_orientation()[1]
                source.set_position_orientation(pose[0], T.quat_multiply(
                    T.quat_conjugate(outlet_orientation), pose[1]))
            ready = []
            for name, conditions in modifier.conditions.items():
                # The destination is routed explicitly. Keep every readiness
                # and capacity condition; only physical projection-overlap is
                # replaced by our destination volume/contact checks.
                checks = conditions[:-1] if modifier.requires_overlap else conditions
                if source.scene.is_physical_particle_system(name) and all(check(source) for check in checks):
                    ready.append(source.scene.get_system(name))
            return modifier, ready
        finally:
            if tilt:
                source.set_position_orientation(*pose)

    def _transfer_material_portion(self, primitive, source, destination, max_steps, *, empty_ok=False):
        from omnigibson.object_states import ContainedParticles, ContactParticles
        from omnigibson.object_states.saturated import ModifiedParticles
        scene = self.env.scene
        # Nested bowls/bags own their contents; pouring an outer tray must not
        # extract material through those intervening objects.
        descendants = [child for child, _, _ in self._grasp_dependencies(source)]
        captured = snapshot_particles(scene, [source, *descendants], include_supported=True)
        captured = [record for record in captured if record.container is source]
        modifier, emitted = (None, [])
        if not captured:
            modifier, emitted = self._ready_dispenser(source, tilt=primitive == 'pour')
        surface = ContainedParticles not in destination.states
        systems = {record.system.name: record.system for record in captured}
        systems.update((system.name, system) for system in emitted)
        if not systems:
            if empty_ok:
                return {'primitive': primitive, 'materials': [], 'source_has_more_material': False,
                        'held_vessel_retained': True, 'postcondition': 'actual_transfer_then_physical_settling'}
            from omnigibson.object_states import ToggledOn
            if modifier is not None and ToggledOn in source.states and not source.states[ToggledOn].get_value():
                raise SkillError('material_dispenser_off',
                    'The material dispenser is switched off; enable it with toggle_on before dispensing')
            raise SkillError('material_source_empty', 'Selected source has no accessible loose material or ready dispenser')
        changes = []
        # No physics until all carry tracking reflects the actual transfer.
        # Once material can cook/mix during stepping, do not roll it back into
        # its earlier identity or silently create replacement ingredients.
        try:
            for name, system in sorted(systems.items()):
                selected = [i for record in captured if record.system is system for i in _indices(scene, record)]
                generating = system in emitted
                available = len(selected)
                if generating:
                    used = int(source.states[ModifiedParticles].get_value(system))
                    available = min(int(modifier._get_max_particles_limit_per_step(system)),
                                    int(modifier.physical_particle_modification_limit) - used)
                sampling = {}
                sampler = free_surface_positions if surface else free_volume_positions
                positions = sampler(scene, destination, system, min(512, available),
                                    diagnostics=sampling) if available else []
                self._placement_record({'status': 'material_volume_sampling', 'primitive': primitive,
                    'source': source.name, 'destination': destination.name, 'material': name,
                    'accepted': len(positions), **sampling})
                orientations = None
                if (not len(positions) and not generating and available
                        and getattr(system, 'particles_view', None) is not None):
                    from .material_geometry import macro_receiving_poses, macro_surface_poses
                    geometry = {}
                    sampler = macro_surface_poses if surface else macro_receiving_poses
                    positions, orientations = sampler(
                        scene, destination, system, min(512, available), diagnostics=geometry)
                    self._placement_record({'status': 'material_geometry_sampling', 'primitive': primitive,
                        'source': source.name, 'destination': destination.name, 'material': name,
                        'accepted': len(positions), **geometry})
                if not len(positions):
                    continue
                before = int(system.n_particles)
                old_view = physical_particle_view(system)
                old_paths = set(old_view.prim_paths) if old_view is not None else set()
                if generating:
                    system.generate_particles(positions=positions)
                    source.states[ModifiedParticles].set_value(system, used + len(positions))
                    indices = [i for i, path in enumerate(physical_particle_view(system).prim_paths)
                               if path not in old_paths]
                else:
                    indices = selected[:len(positions)]
                    move_particle_centers(system, indices, positions, orientations)
                    if int(system.n_particles) != before:
                        raise SkillError('execution_error', 'Transfer unexpectedly changed the material count', changed=True)
                centers = system.get_particles_position_orientation()[0][indices]
                if not surface and not bool(destination.states[ContainedParticles].link.check_points_in_volume(centers).all()):
                    raise SkillError('execution_error', 'Physical transfer did not reach the receiving volume', changed=True)
                view = physical_particle_view(system)
                changes.append(dict(system=system, paths=tuple(view.prim_paths[i] for i in indices),
                                    count=len(indices), generated=generating,
                                    source_remaining=None if generating else available - len(indices)))
        finally:
            held = self._get_held()
        if not changes:
            if empty_ok:
                return {'primitive': primitive, 'materials': [], 'source_has_more_material': True,
                        'held_vessel_retained': True, 'postcondition': 'rigid_transfer_only; material_not_transferred'}
            raise SkillError('sampling_error', 'No collision-free receiving space for a material portion')
        self._placement_record({'status': 'material_transferred', 'primitive': primitive,
            'source': source.name, 'destination': destination.name,
            'materials': [{'system': item['system'].name, 'count': item['count'],
                           'generated_by_authored_source': item['generated']} for item in changes]})
        for _ in range(min(6, max_steps)):
            self._step(self.primitives._empty_action())
        materials = []
        for item in changes:
            system = item['system']
            view = physical_particle_view(system) if scene.active_systems.get(system.name) is system else None
            lookup = {} if view is None else {path: i for i, path in enumerate(view.prim_paths)}
            retained = [lookup[path] for path in item['paths'] if path in lookup]
            # A native cooking/mixing transition may consume these identities.
            # Report it separately; do not claim a final material state.
            transformed = len(retained) != item['count']
            inside = 0
            if retained:
                if surface:
                    contacts = {int(i) for i in destination.states[ContactParticles].get_value(system)}
                    inside = len(contacts.intersection(retained))
                else:
                    centers = system.get_particles_position_orientation()[0][retained]
                    inside = int(destination.states[ContainedParticles].link.check_points_in_volume(centers).sum())
            if not transformed and inside == 0:
                raise SkillError('postcondition_error', 'Transferred portion did not remain at the receiver', changed=True)
            materials.append({'material': system.name, 'moved_particles': item['count'],
                'retained_particles': inside, 'source_remaining_particles': item['source_remaining'],
                'retained_relation': 'surface_contact' if surface else 'inside_volume',
                'identities_changed_during_settling': transformed,
                'generated_by_authored_source': item['generated']})
        moved = {item['system'].name: item['count'] for item in changes if not item['generated']}
        finite_left = sum(len(record.paths) for record in captured) - sum(moved.values())
        source_has_more = True if finite_left > 0 else None if emitted else False
        return {'primitive': primitive, 'implementation': 'checked_physical_material_portion',
                'source_has_more_material': source_has_more,
                'materials': materials, 'postcondition': 'actual_transfer_then_physical_settling',
                'held_vessel_retained': True}
