"""Placement of a carried assembly into model-selected container volumes.

Candidate generation uses private geometry, never task predicates or object IDs.
Official Inside, physical contacts and the caller's payload checks remain the
acceptance criteria. No candidate may change the identity of the selected target.
"""
import itertools
import math
import time

from ..contracts import SkillError


def inset_centers(low, high, extent):
    """Sample the fitting center domain without a fixed clearance requirement.

    A narrow but nonempty domain must reach the physical checks: a fixed inset
    used to reject recorded sandwich/box and magazine/bucket candidates before
    any collision test. Interior fractions avoid starting flush with the volume
    boundary when space exists; they never require extra room to admit a pose.
    """
    lo = [a + e/2 for a, e in zip(low, extent)]
    hi = [a - e/2 for a, e in zip(high, extent)]
    if any(a > b for a, b in zip(lo, hi)):
        return []
    xy = [(x, y) for x, y in itertools.product((.5, .15, .85), repeat=2)]
    return list(dict.fromkeys(
        tuple(a + f*(b-a) for a, b, f in zip(lo, hi, (x, y, z)))
        for x, y in xy for z in (.05, .5, .95)))


def volume_center_domains(low, high, extent):
    """Prefer full-envelope fits without treating annotations as cavity walls.

    Official Inside tests object centers. Its native sampler also falls back
    to the full annotated volume when an inset is empty. These are only pose
    proposals: collision, support and every assembly member's Inside predicate
    must still pass. Interleaving domains reserves work for this fallback even
    when an inset exists but its sampled positions are obstructed.
    """
    preferred = inset_centers(low, high, extent)
    domains = [('inset', preferred)] if preferred else []
    domains.append(('full_volume', inset_centers(low, high, (0., 0., 0.))))
    return domains


def envelope_protrusion(center, extent, low, high, settling_drop=0.):
    """Rank a resting envelope within a volume; never reject conservative bounds.

    Native Inside can accept an item's center while it rests on a box rim and
    obstructs the lid. Prefer lower interior supports for unladen placements.
    Bounds are only a ranking hint: physical contact and Inside remain required.
    """
    resting = (*center[:2], center[2]-settling_drop)
    return sum(max(0., lo-(c-e/2))+max(0., c+e/2-hi)
               for c,e,lo,hi in zip(resting,extent,low,high))


def assembly_orientations(held, root_quat, torch, transforms, reference_quat=None, *, allow_reorientation=False):
    """Resting proposals preserve payloads; unladen items may turn onto a side.

For payloads, compare local gravity and reject flips or large tilts. Unladen
objects may try asset poses or principal faces when metadata is absent; these
are proposals only, with unchanged collision and settling acceptance checks.
"""
    current = transforms.quat2mat(root_quat)
    current_yaw = math.atan2(float(current[1,0]), float(current[0,0]))
    metadata = getattr(held, 'orientations', None)
    options = sorted((metadata or {}).values(), key=lambda value: -value.get('prob', 0))
    if metadata is not None and not options:
        options = [{'rotation':[0.,0.,0.,1.], 'prob':1.}]
    if allow_reorientation and not metadata:
        # Without asset resting metadata, principal faces are pose proposals.
        # Only physical settling plus official containment may accept them.
        options = [{'rotation': transforms.euler2quat(torch.tensor(angles, device=root_quat.device)).tolist(),
                    'prob': 1., 'source': 'unladen_face_proposal'}
                   for angles in ((0.,0.,0.),(math.pi/2,0.,0.),(-math.pi/2,0.,0.),
                                  (0.,math.pi/2,0.),(0.,-math.pi/2,0.),(math.pi,0.,0.))]
    bases = []
    for option in options:
        stable = torch.tensor(option['rotation'], dtype=root_quat.dtype, device=root_quat.device)
        rotation = transforms.quat2mat(stable)
        if not allow_reorientation and float(torch.dot(rotation[2], current[2])) < math.cos(math.radians(45)):
            continue
        angle = current_yaw-math.atan2(float(rotation[1,0]),float(rotation[0,0]))
        yaw = transforms.euler2quat(torch.tensor([0.,0.,angle],device=root_quat.device))
        bases.append((option.get('source','asset_resting_pose'), transforms.quat_multiply(yaw,stable)))
    bases.append(('carried_pose',root_quat))
    angles=[0.,math.pi/2,math.pi,3*math.pi/2]
    if reference_quat is not None:
        reference=transforms.quat2mat(reference_quat)
        aligned=math.atan2(float(reference[1,0]),float(reference[0,0]))-current_yaw
        angles.extend(aligned+i*math.pi/(4 if allow_reorientation else 2)
                      for i in range(8 if allow_reorientation else 4))
    result = []
    for label, base in bases:
        for angle in angles:
            yaw = transforms.euler2quat(torch.tensor([0.,0.,angle],device=root_quat.device))
            quat = transforms.quat_multiply(yaw,base)
            if any(abs(float(torch.dot(quat,other))) > .99999 for _,other,_ in result):
                continue
            result.append((label,quat,angle))
    return result


def sample_assembly_inside(backend, held, target, contents, *, point=None, residents=(),
                           max_candidates=108, max_seconds=30.):
    """Search all selected-container volumes and nearby supported orientations.

Unlike sampling root origins uniformly, bounds refer to the whole assembly
center. Insets are preferred proposals, not mandatory full-mesh containment.
Payload orientations preserve local gravity. Unladen objects may rest on a side.
Rejected candidate poses never become committed actions or task evidence.
"""
    from omnigibson.object_states import Inside, Open
    from omnigibson.utils import transform_utils as T
    from omnigibson.utils.usd_utils import RigidContactAPI
    from .particle_payload import particle_bounds, particles_in_volumes, verify_particles
    from .particle_payload import snapshot_particle_state, particle_state_unchanged, restore_particle_state
    from .placement import scene_object_identities
    torch, sim = backend.torch, backend.og.sim
    start = time.monotonic()
    initial = sim.dump_state(serialized=False)
    initial_objects = scene_object_identities(sim)
    initial_particles = snapshot_particle_state(sim)
    root_pos, root_quat = (v.clone() for v in held.get_position_orientation())
    target_pos, target_quat = (v.clone() for v in target.get_position_orientation())
    was_open = bool(target.states[Open].get_value()) if Open in target.states else None
    assembly = [held, *[obj for obj, _ in contents]]
    particles = getattr(backend, '_placement_particle_payload', (None, []))[1]
    volumes = [link for link in target.links.values() if link.is_meta_link and
               link.meta_link_type in {'fillable', 'openfillable'}]
    volume_poses={link.prim_path:tuple(v.clone() for v in link.get_position_orientation()) for link in volumes}
    if point is not None:
        def distance(link):
            lo, hi = link.visual_aabb
            nearest = torch.maximum(lo, torch.minimum(point.to(lo.device), hi))
            return float(torch.linalg.norm(nearest-point.to(lo.device)))
        volumes.sort(key=distance)
    counts = {}
    domain_attempts = {}
    empty_insets = 0
    attempted = 0
    dirty = False
    accepted = False
    identity_changed = False

    def restore(state=initial, particle_state=initial_particles):
        nonlocal identity_changed
        if scene_object_identities(sim) != initial_objects or not particle_state_unchanged(initial_particles):
            identity_changed = True
            raise SkillError('target_changed', 'Scene identities changed during assembly sampling', changed=True)
        sim.load_state(state, serialized=False)
        restore_particle_state(particle_state)

    open_only = None

    def accept_open_only():
        nonlocal accepted
        if open_only is None:
            return False
        restore(open_only['state'], open_only['particles'])
        accepted = True
        backend._placement_record({'status':'inside_candidate_accepted', 'held':held.name,
            'target':target.name, **open_only['candidate'],
            'closure_clearance':'open_only_fallback',
            'note':'Inside is valid; closing may require rearranging contents'})
        return True

    def check():
        backend.deadline.check(changed=dirty)
        if time.monotonic()-start > max_seconds:
            raise SkillError('sampling_budget_exhausted', 'Assembly volume search exhausted its work budget', changed=dirty)

    def reject(reason, candidate, **details):
        counts[reason] = counts.get(reason, 0)+1
        backend._placement_record({'status': 'inside_candidate_rejected', 'stage': reason,
                                   'held': held.name, 'target': target.name, **candidate, **details})

    try:
        from .closure_clearance import closed_geometry_proposals, closed_geometry_overlap
        closure_geometry=closed_geometry_proposals(backend,target)
        backend._placement_record({'status':'inside_sampling_started', 'held':held.name, 'target':target.name,
                                   'held_pose':[root_pos.cpu().tolist(),root_quat.cpu().tolist()],
                                   'held_joints':held.get_joint_positions().cpu().tolist() if getattr(held,'n_joints',0) else [],
                                   'volumes':[link.prim_path for link in volumes]})
        # Compute envelopes once for each nearby resting/carried orientation. Merely
        # computing candidates must not step physics or load the whole scene.
        groups = []; extents = {}
        resident_bounds=[tuple(v.cpu().tolist() for v in obj.aabb) for obj,_,_ in residents]
        payload_locked = bool(contents or getattr(backend,'_placement_particle_payload',(None,[]))[1])
        for orientation_source, quat, angle in assembly_orientations(
                held,root_quat,torch,T,reference_quat=target_quat,allow_reorientation=not payload_locked):
            check()
            held.set_position_orientation(root_pos, quat)
            backend._relocate_contents(held, contents)
            bounds = [obj.aabb for obj in assembly]
            if particles:
                bounds.extend(particle_bounds(backend.env.scene, particles))
            low = torch.stack([a for a, _ in bounds]).amin(dim=0)
            high = torch.stack([b for _, b in bounds]).amax(dim=0)
            center_offset = (low+high)/2-root_pos
            extent = (high-low).cpu().tolist()
            extents[tuple(quat.cpu().tolist())]=extent
            for volume in volumes:
                lo, hi = volume.visual_aabb
                domains = volume_center_domains(lo.cpu().tolist(), hi.cpu().tolist(), extent)
                if domains[0][0] != 'inset':
                    empty_insets += 1
                    backend._placement_record({'status':'inside_inset_unavailable',
                        'reason':'assembly_extent_exceeds_volume', 'held':held.name, 'target':target.name,
                        'volume':volume.prim_path, 'yaw_radians':angle, 'extent':extent,
                        'volume_extent':(hi-lo).cpu().tolist(), 'fallback':'full_volume'})
                for domain, centers in domains:
                    support=getattr(backend,'_inside_support_centers',None)
                    if support is not None:
                        supported=support(held,target,volume,centers,center_offset,assembly[1:],check,
                                          point=point,**({'residents':residents} if residents else {}))
                        if supported:
                            groups.append([(volume,center,center_offset.clone(),quat.clone(),angle,
                                            orientation_source,'support_'+domain) for center in supported])
                    groups.append([(volume, center, center_offset.clone(), quat.clone(), angle,
                                    orientation_source, domain) for center in centers])
        held.set_position_orientation(root_pos, root_quat)
        backend._relocate_contents(held, contents)
        if payload_locked:
            # Spread early work across positions as well as orientations. Repeating
            # many yaws at one occupied center can exhaust the time budget before
            # trying empty space beside an existing item. Keep each height triplet
            # together so a thick container bottom does not starve higher poses.
            diversified=[]
            for index,group in enumerate(groups):
                if not group:continue
                offset=(index*3)%len(group)
                shifted=group[offset:]+group[:offset]
                diversified.append([shifted[i:i+3] for i in range(0,len(shifted),3)])
            candidates=(c for layer in itertools.zip_longest(*diversified)
                        for chunk in layer if chunk is not None for c in chunk)
        else:
            # Give every supported orientation one actual support proposal before
            # spending trials on arbitrary heights. A three-height coarse-volume
            # block per orientation used to exhaust 108 trials before useful side
            # poses reached physics. Preserve click order for loaded assemblies.
            supported=[group for group in groups if group and group[0][-1].startswith('support_')]
            other=[group for group in groups if group and not group[0][-1].startswith('support_')]
            # In occupied containers, rotating repeatedly at one blocked center
            # spends the whole budget without testing the remaining space. Rank
            # resident overlap only as a heuristic (AABBs are not rejection
            # certificates), then distribute ties over different positions.
            visits={}
            def occupancy(candidate):
                _,center,_,quat,*_=candidate
                extent=extents[tuple(quat.cpu().tolist())]
                volume=max(math.prod(extent),1e-12)
                return sum(math.prod(max(0.,min(c+e/2,b)-max(c-e/2,a))
                                     for c,e,a,b in zip(center,extent,lo,hi))
                           for lo,hi in resident_bounds)/volume
            def location(candidate):return tuple(round(float(v)/.02) for v in candidate[1])
            def top_height(candidate):
                return float(candidate[1][2])+.5*extents[tuple(candidate[3].cpu().tolist())][2]
            def closed_overlap(candidate):
                return round(closed_geometry_overlap(closure_geometry,candidate[0],candidate[1],
                    extents[tuple(candidate[3].cpu().tolist())]),6)
            volume_bounds={v.prim_path:tuple(a.cpu().tolist() for a in v.visual_aabb) for v in volumes}
            settling_drop=.025+.5*9.81*sim.get_physics_dt()**2
            def support_rank(candidate):
                volume,center,_,quat,*_=candidate
                extent=extents[tuple(quat.cpu().tolist())]
                lo,hi=volume_bounds[volume.prim_path]
                return (closed_overlap(candidate), round(envelope_protrusion(center,extent,lo,hi,settling_drop),6),
                        round(occupancy(candidate),6),top_height(candidate) if was_open is True else round(float(center[2]),6),
                        visits.get(location(candidate),0))
            first_support=[];remainder=[]
            for group in supported:
                group=sorted(group,key=support_rank)
                first_support.append(group[0]);remainder.append(group[1:])
                key=location(group[0]);visits[key]=visits.get(key,0)+1
            first_support.sort(key=support_rank)
            remainder+=other
            chunks=[[group[i:i+3] for i in range(0,len(group),3)] for group in remainder if group]
            tail=(c for layer in itertools.zip_longest(*chunks)
                  for chunk in layer if chunk is not None for c in chunk)
            candidates=itertools.chain(first_support,tail)
        seen=set();locations=set();duplicates=0
        for volume, center, offset, quat, angle, orientation_source, domain in candidates:
            check()
            # Inset and full-volume support projections often produce exactly
            # the same pose. One physics trial suffices; do not charge it twice.
            q=quat.cpu().tolist()
            if next((x for x in reversed(q) if abs(x)>1e-8),1.)<0:q=[-x for x in q]
            key=(volume.prim_path,*(round(float(c-o),5) for c,o in zip(center,offset)),
                 *(round(float(v),5) for v in q))
            if key in seen:
                duplicates+=1;continue
            seen.add(key)
            if attempted >= max_candidates: break
            attempted += 1
            locations.add(tuple(round(float(v),4) for v in center[:2]))
            domain_attempts[domain] = domain_attempts.get(domain, 0)+1
            candidate = {'candidate': attempted, 'volume': volume.prim_path, 'center': center,
                         'center_domain':domain,
                         'yaw_radians': angle,'orientation_source':orientation_source,'orientation':quat.cpu().tolist()}
            center = torch.tensor(center, dtype=root_pos.dtype, device=root_pos.device)
            admission_center = center.clone()
            if domain.startswith('support_'):
                admission_center[2] -= .025+.5*9.81*sim.get_physics_dt()**2
            if not bool(volume.check_points_in_volume(admission_center.unsqueeze(0)).item()):
                reject('outside_fillable_volume', candidate)
                continue
            if dirty:
                restore()
            dirty = True
            held.set_position_orientation(center-offset, quat)
            held.keep_still()
            backend._relocate_contents(held, contents)
            sim.step_physics()
            def contact():
                return RigidContactAPI.is_in_contact(scene_idx=held.scene.idx, query_set=[held],
                                                     with_set=None, ignore_set=None, current_only=True)
            if contact():
                reject('initial_collision', candidate, contact_bodies=backend._placement_contacts(held, assembly[1:]))
                continue
            limit = max(1, int(.5/sim.get_physics_dt()))
            for _ in range(limit):
                check(); sim.step_physics()
                if contact(): break
            else:
                reject('no_support_contact', candidate)
                continue
            for _ in range(5):
                check(); sim.step_physics()
            for _ in range(limit):
                if float(torch.linalg.norm(held.get_linear_velocity())) <= .001: break
                check(); sim.step_physics()
            pos, orn = target.get_position_orientation()
            if float(torch.linalg.norm(pos-target_pos)) > .05 or float(T.get_orientation_diff_in_radian(orn,target_quat)) > math.radians(10):
                reject('container_moved', candidate)
                continue
            # A passive lid may settle while the box's usable interior stays
            # still. Reject motion of the selected volume (e.g. a sliding
            # drawer), not unrelated articulated joints elsewhere on the asset.
            volume_pos,volume_quat=volume.get_position_orientation()
            before_pos,before_quat=volume_poses[volume.prim_path]
            if float(torch.linalg.norm(volume_pos-before_pos))>.05 or \
                    float(T.get_orientation_diff_in_radian(volume_quat,before_quat))>math.radians(10):
                reject('container_volume_moved',candidate)
                continue
            if was_open is True and not target.states[Open].get_value():
                reject('container_closed_during_sampling',candidate)
                continue
            backend._relocate_contents(held, contents)
            if not held.states[Inside].get_value(target):
                reject('official_inside_false', candidate)
                continue
            if any(not obj.states[Inside].get_value(target) for obj in assembly[1:]):
                reject('payload_outside_destination', candidate)
                continue
            if particles:
                if not particles_in_volumes(backend.env.scene, particles, [volume]):
                    reject('particles_outside_destination', candidate)
                    continue
                try:
                    verify_particles(backend.env.scene, particles)
                except SkillError as exc:
                    if exc.code != 'postcondition_error':
                        raise
                    reject('particle_payload_postcondition', candidate, error=str(exc))
                    continue
            from .closure_clearance import check_closure_clearance
            closure = check_closure_clearance(backend, target, assembly)
            if not closure['clear']:
                # A lid-compatible placement is preferable, but Inside does
                # not promise that an oversized object allows the lid to close.
                # Keep a physically valid open placement if no better one is
                # found within the existing bounded search.
                if open_only is None:
                    open_only={'state':sim.dump_state(serialized=False),
                               'particles':snapshot_particle_state(sim), 'candidate':candidate}
                reject('closed_articulation_collision', candidate, closure=closure)
                continue
            accepted = True
            backend._placement_record({'status':'inside_candidate_accepted', 'held':held.name,
                                       'target':target.name, **candidate})
            return True
        return accept_open_only()
    except SkillError as exc:
        identity_changed = identity_changed or exc.code == 'target_changed'
        if exc.code == 'sampling_budget_exhausted' and open_only is not None:
            backend.deadline.check(changed=True)
            return accept_open_only()
        raise
    finally:
        try:
            if not accepted and not identity_changed:
                if dirty:
                    restore()
                else:
                    held.set_position_orientation(root_pos, root_quat)
                    backend._relocate_contents(held, contents)
        finally:
            backend._placement_record({'status':'inside_sampling_summary', 'method':'assembly_volume_search',
                                       'accepted':accepted, 'attempted':attempted, 'rejections':counts,
                                       'empty_insets':empty_insets, 'domain_attempts':domain_attempts,
                                       'unique_xy_attempted':len(locations) if 'locations' in locals() else 0,
                                       'duplicate_poses_skipped':duplicates if 'duplicates' in locals() else 0,
                                       'seconds':time.monotonic()-start, 'held':held.name, 'target':target.name,
                                       'identity_changed':identity_changed})
