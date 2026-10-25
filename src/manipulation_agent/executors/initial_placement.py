"""Reuse checked container placement during sampled-instance construction.

This policy is only installed by the offline constructor, never by the actor.
It preserves previously placed residents and provides geometric proposals when
the native random Inside sampler fails. Initial predicates remain mandatory.
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
        self._base_target = self._object_anchor = self._local_operation = None
        self._placement_particle_payload = (None, [])
        self._stabilized_containers = {}

    def _restore_stabilized_containers(self):
        # Construction pins only the selected container and its prior residents.
        # No runtime navigation stabilization exists in this adapter.
        pass


@contextmanager
def checked_initial_inside(env, output):
    """Process-local initial sampler extension with native acceptance checks."""
    from omnigibson.object_states import Inside
    from .inside_sampling import sample_assembly_inside
    from .particle_payload import snapshot_particle_state, restore_particle_state
    from .particle_payload import particle_state_unchanged
    adapter = _InitialPlacementAdapter(env, output)
    sim = adapter.og.sim
    original = Inside._set_value

    class NativeWorkLimit(Exception):
        pass

    def sample(state, target, new_value, **kwargs):
        if not new_value:
            return original(state, target, new_value, **kwargs)
        held = state.obj
        residents = [row for row in adapter._container_payload(target) if row[0] is not held]
        root = tuple(v.clone() for v in target.get_position_orientation())
        prior_anchor = adapter._object_anchor
        adapter._object_anchor = (target, root)
        physics = sim.step_physics
        particles = snapshot_particle_state(sim)
        checkpoint = sim.dump_state(serialized=False)
        started = time.monotonic()
        native = True
        steps = 0

        def pin():
            adapter._restore_object_anchor()
            adapter._relocate_container_payload(residents)

        def step(*args, **kw):
            nonlocal steps
            if native and (steps >= 1200 or time.monotonic() - started > 20.):
                raise NativeWorkLimit()
            adapter.deadline.check(changed=True)
            pin()
            try:
                result = physics(*args, **kw)
                steps += 1
                adapter.sampling_physics_steps += 1
                sim.sync_physx_to_fabric()
                return result
            finally:
                pin()

        def restore():
            if not particle_state_unchanged(particles):
                raise SkillError('target_changed', 'Initial placement changed existing material identities', changed=True)
            sim.load_state(checkpoint, serialized=False)
            restore_particle_state(particles)

        sim.step_physics = step
        adapter._placement_record({'status': 'initial_inside_started', 'held': held.name,
            'target': target.name, 'residents': [obj.name for obj, _, _ in residents],
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
                accepted = sample_assembly_inside(adapter, held, target, [], residents=residents,
                                                  max_candidates=108, max_seconds=45.)
            except SkillError as exc:
                if exc.code != 'sampling_budget_exhausted':
                    raise
                adapter._placement_record({'status': 'initial_geometric_work_limit',
                                           'held': held.name, 'target': target.name})
                accepted = False
            if accepted and kwargs.get('use_trav_map', False):
                from omnigibson.utils.object_state_utils import (
                    get_reachability_sampling_context, is_pose_reachable_for_predicate)
                context = get_reachability_sampling_context(target, 'inside')
                accepted = is_pose_reachable_for_predicate(held.get_position_orientation()[0],
                                                           target, 'inside', context)
                if not accepted:
                    restore()
            return bool(accepted)
        finally:
            sim.step_physics = physics
            adapter._object_anchor = prior_anchor

    Inside._set_value = sample
    try:
        yield adapter
    finally:
        Inside._set_value = original
        adapter._close_placement_record()
