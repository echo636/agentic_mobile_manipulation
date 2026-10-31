"""Offline initial OnTop proposals using the runtime surface geometry checks."""
import itertools
import time
from contextlib import contextmanager


@contextmanager
def native_initial_surface_proposals():
    """Allow native centered proposals when a curved support rejects a cuboid.

    Used only during offline scene construction. Native falling and OnTop
    evaluation still decide success; the centered fallback must also pass
    the reachability check that native sample_kinematics omits on that path.
    """
    from omnigibson.object_states import on_top
    from omnigibson.utils.constants import GROUND_CATEGORIES
    from omnigibson.utils.object_state_utils import (
        get_reachability_sampling_context, is_pose_reachable_for_predicate)
    original = on_top.sample_kinematics

    def sample(predicate, objA, objB, **kwargs):
        if objB.category in GROUND_CATEGORIES:
            return original(predicate, objA, objB, **kwargs)
        accepted = original(predicate, objA, objB, **dict(kwargs, use_last_ditch_effort=True))
        if accepted and kwargs.get('use_trav_map', True):
            context = kwargs.get('reachability_context')
            if context is None:
                context = get_reachability_sampling_context(objB, predicate)
            accepted = is_pose_reachable_for_predicate(
                objA.get_position_orientation()[0], objB, predicate, context)
        return bool(accepted)

    on_top.sample_kinematics = sample
    try:
        yield
    finally:
        on_top.sample_kinematics = original


def surface_proposal_points(low, high, torch, reachability_context=None):
    """Cover the reachable part of a support, including narrow edge strips."""
    if reachability_context is None or reachability_context['has_prismatic_joint']:
        return [torch.stack((low[0] + x * (high[0] - low[0]),
                             low[1] + y * (high[1] - low[1]), high[2]))
                for x, y in itertools.product((.5, .2, .8), repeat=2)]
    trav = reachability_context['trav_map']
    reachable = reachability_context['reachability_map']
    corners = torch.stack([trav.world_to_map(p[:2]) for p in (low, high)])
    start = corners.min(dim=0).values.clamp(min=0)
    end = torch.minimum(corners.max(dim=0).values + 1,
                        corners.new_tensor(reachable.shape))
    cells = (reachable[int(start[0]):int(end[0]), int(start[1]):int(end[1])] == 255).nonzero()
    xy = trav.map_to_world(cells + start.to(cells)).to(low)
    xy = xy[((xy >= low[:2]) & (xy <= high[:2])).all(dim=1)]
    # A bounded farthest-point set covers the admissible area instead of
    # spending physical trials on the same unreachable center each time.
    distance = ((xy - (low[:2] + high[:2]) / 2)**2).sum(dim=1)
    points = []
    for _ in range(min(12, len(xy))):
        index = int(distance.argmin() if not points else distance.argmax())
        point = xy[index]
        points.append(torch.cat((point, high[2:3])))
        new_distance = ((xy - point)**2).sum(dim=1)
        distance = new_distance if len(points) == 1 else torch.minimum(distance, new_distance)
    return points


def sample_initial_surface(backend, held, target, *, max_candidates=48, max_seconds=60.,
                           reachability_context=None):
    from omnigibson.object_states import OnTop
    from omnigibson.utils.object_state_utils import is_pose_reachable_for_predicate
    from .particle_payload import snapshot_particle_state, restore_particle_state, particle_state_unchanged
    from .resting_motion import RestingMotionWindow

    sim, torch = backend.og.sim, backend.torch
    particles = snapshot_particle_state(sim)
    initial = sim.dump_state(serialized=False)
    accepted = False
    started = time.monotonic()
    attempted = 0
    try:
        low, high = target.aabb
        # Cover the support itself. Bounds only generate ray origins; every
        # proposal must hit an actual upward-facing collider of this target.
        points = surface_proposal_points(low, high, torch, reachability_context)
        candidates = []
        for angle in (None, 90., 180., 270.):
            for point in points:
                backend.deadline.check(changed=True)
                candidates.extend(backend._surface_candidates(
                    held, target, point, angle, offsets=[(0., 0.)]))
        ranker = backend._inside_pose_overlap_ranker(held, [held])
        if ranker is not None:
            candidates.sort(key=lambda c: ranker(c['pose'][0].tolist(), c['pose'][1]))
        seen = set()
        for candidate in candidates:
            if not is_pose_reachable_for_predicate(
                    candidate['pose'][0], target, 'onTop', reachability_context):
                continue
            key = tuple(round(float(v), 5) for array in candidate['pose'] for v in array)
            if key in seen:
                continue
            seen.add(key)
            if attempted >= max_candidates or (attempted and time.monotonic() - started > max_seconds):
                break
            if not particle_state_unchanged(particles):
                raise RuntimeError('Initial surface trial changed material identities')
            sim.load_state(initial, serialized=False)
            restore_particle_state(particles)
            attempted += 1
            held.set_position_orientation(*candidate['pose'])
            held.keep_still()
            held.wake()
            sim.step_physics()
            if not backend._placement_contact_report(held)['admissible']:
                backend._placement_record({'status': 'initial_surface_rejected', 'stage': 'initial_collision',
                    'held': held.name, 'target': target.name, 'candidate': attempted})
                continue
            control_dt, physics_dt = sim.get_sim_step_dt(), sim.get_physics_dt()
            resting = RestingMotionWindow(control_dt)
            for _ in range(max(1, round(2. / control_dt))):
                for _ in range(max(1, round(control_dt / physics_dt))):
                    sim.step_physics()
                resting.add(*held.get_position_orientation())
                if resting.report()['stable']:
                    break
            held.wake()
            for _ in range(5):
                sim.step_physics()
            held.clear_states_cache()
            target.clear_states_cache()
            official = bool(held.states[OnTop].get_value(target))
            contact = backend._placement_contact_report(held, allow_sleeping=True)
            reachable = bool(is_pose_reachable_for_predicate(
                held.get_position_orientation()[0], target, 'onTop', reachability_context))
            accepted = official and resting.report()['stable'] and contact['admissible'] and reachable
            backend._placement_record({'status': 'initial_surface_accepted' if accepted else 'initial_surface_rejected',
                'held': held.name, 'target': target.name, 'candidate': attempted,
                'official_on_top': official, 'reachable': reachable,
                'resting_motion': resting.report(), 'contact_geometry': contact,
                'settled_pose': [v.tolist() for v in held.get_position_orientation()]})
            if accepted:
                return True
        return False
    finally:
        if not accepted:
            sim.load_state(initial, serialized=False)
            restore_particle_state(particles)
        backend._placement_record({'status': 'initial_surface_summary', 'held': held.name,
            'target': target.name, 'accepted': accepted, 'attempted': attempted,
            'seconds': time.monotonic() - started})
