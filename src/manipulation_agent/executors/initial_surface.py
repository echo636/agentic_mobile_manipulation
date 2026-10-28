"""Offline initial OnTop proposals using the runtime surface geometry checks."""
import itertools
import time


def sample_initial_surface(backend, held, target, *, max_candidates=48, max_seconds=60.):
    from omnigibson.object_states import OnTop
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
        points = [torch.stack((low[0] + x * (high[0] - low[0]),
                               low[1] + y * (high[1] - low[1]), high[2]))
                  for x, y in itertools.product((.5, .2, .8), repeat=2)]
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
            accepted = official and resting.report()['stable'] and contact['admissible']
            backend._placement_record({'status': 'initial_surface_accepted' if accepted else 'initial_surface_rejected',
                'held': held.name, 'target': target.name, 'candidate': attempted,
                'official_on_top': official, 'resting_motion': resting.report(), 'contact_geometry': contact,
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
