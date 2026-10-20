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


def assembly_orientations(held, root_quat, torch, transforms, reference_quat=None):
    """Nearby asset-supported resting poses may correct carry-induced tilt.

Compare gravity in the object's local frame, so arbitrary yaw cannot exclude a
valid resting pose. Reject flips/large tilts; contents follow the same rigid
rotation and still must pass post-placement containment checks.
"""
    current = transforms.quat2mat(root_quat)
    current_yaw = math.atan2(float(current[1,0]), float(current[0,0]))
    metadata = getattr(held, 'orientations', None)
    options = sorted((metadata or {}).values(), key=lambda value: -value.get('prob', 0))
    if metadata is not None and not options:
        options = [{'rotation':[0.,0.,0.,1.], 'prob':1.}]
    bases = []
    for option in options:
        stable = torch.tensor(option['rotation'], dtype=root_quat.dtype, device=root_quat.device)
        rotation = transforms.quat2mat(stable)
        if float(torch.dot(rotation[2], current[2])) < math.cos(math.radians(45)):
            continue
        angle = current_yaw-math.atan2(float(rotation[1,0]),float(rotation[0,0]))
        yaw = transforms.euler2quat(torch.tensor([0.,0.,angle],device=root_quat.device))
        bases.append(('asset_resting_pose', transforms.quat_multiply(yaw,stable)))
    bases.append(('carried_pose',root_quat))
    angles=[0.,math.pi/2,math.pi,3*math.pi/2]
    if reference_quat is not None:
        reference=transforms.quat2mat(reference_quat)
        aligned=math.atan2(float(reference[1,0]),float(reference[0,0]))-current_yaw
        angles.extend(aligned+a for a in (0.,math.pi/2,math.pi,3*math.pi/2))
    result = []
    for label, base in bases:
        for angle in angles:
            yaw = transforms.euler2quat(torch.tensor([0.,0.,angle],device=root_quat.device))
            quat = transforms.quat_multiply(yaw,base)
            if any(abs(float(torch.dot(quat,other))) > .99999 for _,other,_ in result):
                continue
            result.append((label,quat,angle))
    return result


def sample_assembly_inside(backend, held, target, contents, *, point=None,
                           max_candidates=108, max_seconds=30.):
    """Search all selected-container volumes and nearby supported orientations.

Unlike sampling root origins uniformly, inset bounds refer to the whole assembly
center. Orientations preserve every carried relative pose and never flip it.
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
    attempted = 0
    dirty = False
    accepted = False
    identity_changed = False

    def restore():
        if scene_object_identities(sim) != initial_objects or not particle_state_unchanged(initial_particles):
            raise SkillError('target_changed', 'Scene identities changed during assembly sampling', changed=True)
        sim.load_state(initial, serialized=False)
        restore_particle_state(initial_particles)

    def check():
        backend.deadline.check(changed=dirty)
        if time.monotonic()-start > max_seconds:
            raise SkillError('sampling_budget_exhausted', 'Assembly volume search exhausted its work budget', changed=dirty)

    def reject(reason, candidate, **details):
        counts[reason] = counts.get(reason, 0)+1
        backend._placement_record({'status': 'inside_candidate_rejected', 'stage': reason,
                                   'held': held.name, 'target': target.name, **candidate, **details})

    try:
        backend._placement_record({'status':'inside_sampling_started', 'held':held.name, 'target':target.name,
                                   'held_pose':[root_pos.cpu().tolist(),root_quat.cpu().tolist()],
                                   'held_joints':held.get_joint_positions().cpu().tolist() if getattr(held,'n_joints',0) else [],
                                   'volumes':[link.prim_path for link in volumes]})
        # Compute envelopes once for each nearby resting/carried orientation. Merely
        # computing candidates must not step physics or load the whole scene.
        groups = []
        for orientation_source, quat, angle in assembly_orientations(held,root_quat,torch,T,reference_quat=target_quat):
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
            for volume in volumes:
                lo, hi = volume.visual_aabb
                centers = inset_centers(lo.cpu().tolist(), hi.cpu().tolist(), extent)
                if not centers:
                    reject('assembly_extent_exceeds_volume', {'volume': volume.prim_path,
                           'yaw_radians': angle}, extent=extent, volume_extent=(hi-lo).cpu().tolist())
                groups.append([(volume, center, center_offset.clone(), quat.clone(), angle,orientation_source) for center in centers])
        held.set_position_orientation(root_pos, root_quat)
        backend._relocate_contents(held, contents)
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
        for volume, center, offset, quat, angle, orientation_source in candidates:
            check()
            if attempted >= max_candidates: break
            attempted += 1
            candidate = {'candidate': attempted, 'volume': volume.prim_path, 'center': center,
                         'yaw_radians': angle,'orientation_source':orientation_source,'orientation':quat.cpu().tolist()}
            center = torch.tensor(center, dtype=root_pos.dtype, device=root_pos.device)
            if not bool(volume.check_points_in_volume(center.unsqueeze(0)).item()):
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
            accepted = True
            backend._placement_record({'status':'inside_candidate_accepted', 'held':held.name,
                                       'target':target.name, **candidate})
            return True
        return False
    except SkillError as exc:
        identity_changed = exc.code == 'target_changed'
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
                                       'seconds':time.monotonic()-start, 'held':held.name, 'target':target.name,
                                       'identity_changed':identity_changed})
