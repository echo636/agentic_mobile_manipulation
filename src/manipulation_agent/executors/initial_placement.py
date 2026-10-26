"""Reuse checked placement during sampled-instance construction.

This policy is only installed by the offline constructor, never by the actor.
It preserves previously placed residents and provides geometric proposals when
native Inside / OnTop sampling fails. Initial predicates remain mandatory.
"""
from contextlib import contextmanager
import time

from ..contracts import SkillError
from ..deadline import EpisodeDeadline
from .carry import ControlledCarry
from .placement import CheckedPlacement


class _InitialPlacementAdapter(CheckedPlacement, ControlledCarry):
    def __init__(self, env, output):
        import torch
        import omnigibson as og
        self.env, self.output, self.torch, self.og = env, output, torch, og
        self.robot = env.robots[0]
        self.deadline = EpisodeDeadline.from_env()
        self.steps = self.sampling_physics_steps = 0
        self._local_operation = None
        self._placement_particle_payload = (None, [])
        self.sampling_active = False



@contextmanager
def checked_initial_inside(env, output):
    """Process-local placement extension; historical name kept for constructors."""
    from omnigibson.object_states import Inside, OnTop
    from omnigibson.utils.bddl_utils import BDDLSampler
    from .inside_sampling import sample_assembly_inside
    from .particle_payload import snapshot_particle_state, restore_particle_state
    from .particle_payload import particle_state_unchanged
    adapter = _InitialPlacementAdapter(env, output)
    sim = adapter.og.sim
    originals = {Inside: Inside._set_value, OnTop: OnTop._set_value}
    original_final = BDDLSampler._sample_initial_conditions_final

    def final_conditions(sampler, *args, **kwargs):
        previous = adapter.sampling_active
        adapter.sampling_active = True
        try:
            error = original_final(sampler, *args, **kwargs)
            if error:
                # Task loading resets the scene after a failed sampler. Capture
                # the physical failure here, while the actual trial scene still
                # exists, rather than saving the reset off-scene import poses.
                from ..records import write_json
                checkpoint_dir = output / 'partial_initial_instance'
                try:
                    env.task.save_task(env, save_dir=str(checkpoint_dir), task_relevant_only=False)
                    write_json(output / 'partial_initial_checkpoint.json', {
                        'status': 'passed', 'sampling_error': str(error),
                        'files': sorted(p.name for p in checkpoint_dir.glob('*.json')),
                        'interpretation': 'Incomplete construction checkpoint; no valid episode or task success'})
                except Exception as exc:
                    write_json(output / 'partial_initial_checkpoint.json', {
                        'status': 'failed', 'sampling_error': str(error), 'error': str(exc)})
            return error
        finally:
            adapter.sampling_active = previous

    class NativeWorkLimit(Exception):
        pass

    def sample(state, target, new_value, **kwargs):
        # The earlier fixture-assignment pass probes alternative parents in
        # temporary scenes. Incidental contents there are not committed initial
        # placements; pinning them changes the feasibility probe and can trigger
        # repeated whole-room retries. Extend only the final construction pass
        # (or an explicitly enabled post-reset repair of its initial literals).
        relation = Inside if isinstance(state, Inside) else OnTop
        original = originals[relation]
        if not new_value or not adapter.sampling_active or state.obj is adapter.robot:
            return original(state, target, new_value, **kwargs)
        held = state.obj
        residents = [row for row in adapter._container_payload(target) if row[0] is not held]
        physics = sim.step_physics
        particles = snapshot_particle_state(sim)
        checkpoint = sim.dump_state(serialized=False)
        started = time.monotonic()
        native = True
        steps = 0

        def step(*args, **kw):
            nonlocal steps
            if native and (steps >= 1200 or time.monotonic() - started > 20.):
                raise NativeWorkLimit()
            adapter.deadline.check(changed=True)
            result = physics(*args, **kw)
            steps += 1
            adapter.sampling_physics_steps += 1
            sim.sync_physx_to_fabric()
            return result

        def restore():
            if not particle_state_unchanged(particles):
                raise SkillError('target_changed', 'Initial placement changed existing material identities', changed=True)
            sim.load_state(checkpoint, serialized=False)
            restore_particle_state(particles)

        sim.step_physics = step
        adapter._placement_record({'status': 'initial_inside_started', 'held': held.name,
            'target': target.name, 'relation': relation.__name__, 'residents': [obj.name for obj, _, _ in residents],
            'object_extent': held.aabb_extent.tolist(),
            'fillable_bounds': [[v.tolist() for v in link.visual_aabb] for link in target.links.values()
                                if link.is_meta_link and link.meta_link_type in {'fillable', 'openfillable'}]})
        try:
            try:
                accepted = original(state, target, new_value, **kwargs)
            except NativeWorkLimit:
                accepted = False
                adapter._placement_record({'status': 'initial_native_work_limit', 'held': held.name,
                                           'target': target.name, 'physics_steps': steps})
            if accepted:
                return True
            restore()
            native = False
            try:
                if relation is Inside:
                    # Known resting poses deserve spatial coverage before
                    # generating hundreds of sideways/leaning alternatives.
                    # The latter remain available for long objects that need
                    # a container wall for support. This staged construction
                    # avoids spending a tiny flat object's entire budget on
                    # orientation preparation and its first three faces.
                    for phase in ('resting', 'additional'):
                        adapter._placement_record({'status': 'initial_inside_orientation_phase',
                            'held': held.name, 'target': target.name, 'phase': phase})
                        try:
                            accepted = sample_assembly_inside(adapter, held, target, [], residents=residents,
                                max_candidates=108, max_seconds=45., orientation_phase=phase)
                        except SkillError as exc:
                            if exc.code != 'sampling_budget_exhausted':
                                raise
                            accepted = False
                        if accepted:
                            break
                else:
                    from .initial_surface import sample_initial_surface
                    accepted = sample_initial_surface(adapter, held, target)
            except SkillError as exc:
                if exc.code != 'sampling_budget_exhausted':
                    raise
                adapter._placement_record({'status': 'initial_geometric_work_limit',
                                           'held': held.name, 'target': target.name})
                accepted = False
            if accepted and kwargs.get('use_trav_map', False):
                from omnigibson.utils.object_state_utils import (
                    get_reachability_sampling_context, is_pose_reachable_for_predicate)
                predicate = 'inside' if relation is Inside else 'onTop'
                context = get_reachability_sampling_context(target, predicate)
                accepted = is_pose_reachable_for_predicate(held.get_position_orientation()[0],
                                                           target, predicate, context)
                if not accepted:
                    adapter._placement_record({'status': 'initial_reachability_rejected',
                        'held': held.name, 'target': target.name, 'relation': predicate})
                    restore()
            return bool(accepted)
        finally:
            sim.step_physics = physics

    Inside._set_value = sample
    OnTop._set_value = sample
    BDDLSampler._sample_initial_conditions_final = final_conditions
    try:
        from .initial_reachability import metric_initial_reachability
        with metric_initial_reachability(adapter._placement_record):
            yield adapter
    finally:
        for relation, original in originals.items():
            relation._set_value = original
        BDDLSampler._sample_initial_conditions_final = original_final
        adapter._close_placement_record()
