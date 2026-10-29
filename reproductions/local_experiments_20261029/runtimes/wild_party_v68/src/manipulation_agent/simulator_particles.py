"""Explicit native GPU-particle runtime with USD readback and synchronized geometry.

PhysX GPU dynamics remains enabled. CPU tensor transport permits native particle
readback; PhysX Fabric output is disabled while the renderer's USD/Fabric stage
still receives the actual native state after each physics advance.
"""
from functools import wraps
from contextlib import contextmanager
from pathlib import Path
import atexit
import json
import os
import numpy as np

PROTOCOL = 'native_gpu_particles_usd_readback_v5'
_LAST_ATTACH_GUARD = None

def attach_finite_guard():
    if _LAST_ATTACH_GUARD is None:
        return False
    _LAST_ATTACH_GUARD()
    return True


def install(record_path, *, solver_type=None):
    if solver_type not in (None, 'PGS', 'TGS'):
        raise ValueError('Unsupported native physics solver')
    import omnigibson as og
    from omnigibson.macros import gm
    import carb.settings

    if og.sim is not None or getattr(og.launch, '_manipulation_particle_readback', False):
        raise RuntimeError('Particle runtime must be installed once before simulator construction')
    os.environ['OG_DISABLE_FABRIC'] = '1'
    original = og.launch
    path = Path(record_path)
    record = {'protocol': PROTOCOL, 'status': 'installed', 'geometry_syncs': 0}
    def save():
        path.write_text(json.dumps(record, indent=2) + '\n')
    save()
    @wraps(original)
    def launch(*args, **kwargs):
        if kwargs.get('device') != 'cpu' or not gm.USE_GPU_DYNAMICS:
            raise ValueError('Native particle runtime requires CPU tensors and GPU dynamics')
        value = original(*args, **kwargs)
        sim = og.sim
        # An off faucet must not emit during startup settling. Preserve the
        # source's step bookkeeping while skipping its generation path.
        from omnigibson.object_states import particle_source_or_sink, ToggledOn
        source_update = particle_source_or_sink.ParticleSource._update
        def guarded_source_update(source):
            try:
                if ToggledOn in source.obj.states and not bool(source.obj.states[ToggledOn].get_value()):
                    source._current_step = (source._current_step + 1) % source.n_steps_per_modification
                    source.obj.state_updated()
                    return
            except Exception:
                pass
            return source_update(source)
        particle_source_or_sink.ParticleSource._update = guarded_source_update
        source_limit = int(os.environ.get('MAS_WATER_SOURCE_PARTICLES_PER_STEP', '-1'))
        if source_limit >= 0:
            particle_source_or_sink.m.MAX_SOURCE_PARTICLES_PER_STEP = source_limit
            record['water_source_particles_per_step'] = source_limit
        # The installed NumPy controller bridge chooses CUDA from GPU dynamics,
        # although its PhysX tensor view is on CPU in this runtime.
        import torch
        from omnigibson.utils.backend_utils import _compute_backend
        _compute_backend.to_torch = lambda data: torch.as_tensor(data, device=sim.device)
        # Vt.QuathArray.FromNumpy uses xyzw, as does the native getter.
        # The installed instancer setter incorrectly permutes to wxyz.
        from omnigibson.systems.micro_particle_system import PhysxParticleInstancer
        import omnigibson.lazy as lazy
        orientations = PhysxParticleInstancer.particle_orientations
        def set_orientations(instancer, quat):
            if quat.shape[0] != instancer.n_particles:
                raise ValueError('Native particle quaternion count mismatch')
            values = (lazy.pxr.Vt.QuathArray.FromNumpy(quat.detach().cpu().numpy())
                      if instancer.n_particles else lazy.pxr.Vt.QuathArray())
            instancer.set_attribute(attr='orientations', val=values)
        PhysxParticleInstancer.particle_orientations = property(
            orientations.fget, set_orientations, orientations.fdel, orientations.__doc__)
        settings = carb.settings.get_settings()
        with sim.editing_usd():
            settings.set_bool('/physics/updateToUsd', True)
            settings.set_bool('/physics/updateParticlesToUsd', True)
            if solver_type is not None:
                sim.get_physics_context().set_solver_type(solver_type)
        if settings.get_as_bool('/physics/suppressReadback'):
            raise RuntimeError('Native particle readback is still suppressed')
        from omni.physx import get_physx_interface
        physx = get_physx_interface()
        restore_depth = 0
        synchronizing = False
        finite_guard_enabled = os.environ.get('MAS_PARTICLE_FINITE_GUARD', '').strip().lower() in {'1','true','yes','on'}
        finite_guard_interval = max(1, int(os.environ.get('MAS_PARTICLE_FINITE_GUARD_INTERVAL', '4')))
        finite_guard_counter = 0
        finite_guard_cache = {}
        finite_usd_cache = {}
        record.update(finite_guard_enabled=finite_guard_enabled, finite_guard_interval=finite_guard_interval,
                      finite_guard_checks=0, finite_guard_recoveries=0, finite_guard_failures=0)
        def finite_particle_guard():
            """Detect and restore non-finite native water particles before USD sync.

            This is an explicitly versioned diagnostic candidate. It retains the
            latest finite native positions/orientations and records every restore;
            it never invents particles or changes task predicates.
            """
            if not finite_guard_enabled:
                return
            record['finite_guard_checks'] += 1
            def safe_count(candidate):
                try:
                    return int(getattr(candidate, 'n_particles'))
                except (AttributeError, NotImplementedError, TypeError, ValueError):
                    return -1
            for scene in getattr(sim, 'scenes', ()):
                systems = list(getattr(getattr(scene, 'system_registry', None), 'objects', ()))
                record.setdefault('finite_guard_system_inventory', [])
                for candidate in systems:
                    item = (str(getattr(candidate, 'name', '')), type(candidate).__name__, safe_count(candidate))
                    if item not in record['finite_guard_system_inventory']:
                        record['finite_guard_system_inventory'].append(item)
                active = getattr(scene, 'active_systems', {})
                if hasattr(active, 'values'):
                    active_values = list(active.values())
                    for candidate in active_values:
                        item = (str(getattr(candidate, 'name', '')), type(candidate).__name__, safe_count(candidate))
                        if item not in record['finite_guard_system_inventory']:
                            record['finite_guard_system_inventory'].append(item)
                    systems.extend(active_values)
                seen = set()
                for system in systems:
                    if id(system) in seen:
                        continue
                    seen.add(id(system))
                    name = str(getattr(system, 'name', '')).lower()
                    if 'water' not in name:
                        continue
                    try:
                        positions, orientations = system.get_particles_position_orientation()
                        finite = bool(torch.isfinite(positions).all() and torch.isfinite(orientations).all()
                                      and (positions.abs() < 1e6).all())
                        key = f'{getattr(scene, "scene_idx", 0)}/{system.name}'
                        if finite:
                            finite_guard_cache[key] = (positions.detach().clone(), orientations.detach().clone())
                            continue
                        prior = finite_guard_cache.get(key)
                        if prior is None:
                            record['finite_guard_failures'] += 1
                            continue
                        system.set_particles_position_orientation(positions=prior[0], orientations=prior[1])
                        record['finite_guard_recoveries'] += 1
                        record.setdefault('finite_guard_recovery_systems', []).append(key)
                    except Exception as exc:
                        record['finite_guard_failures'] += 1
                        record.setdefault('finite_guard_errors', []).append(type(exc).__name__)
            finite_usd_water_guard()
            save()

        def finite_usd_water_guard(*, force_restore=False):
            """Check the water PointInstancer that is absent from Python registries."""
            try:
                from omnigibson import lazy
                for prim in sim.stage.Traverse():
                    path = str(prim.GetPath())
                    if '/water/' not in path or str(prim.GetTypeName()) != 'PointInstancer':
                        continue
                    cache = finite_usd_cache.setdefault(path, {})
                    bad = False
                    for name in ('positions', 'orientations', 'scales', 'velocities'):
                        attr = prim.GetAttribute(name)
                        if not attr:
                            continue
                        value = attr.Get()
                        if value is None:
                            continue
                        arr = np.asarray(value)
                        finite = bool(np.isfinite(arr).all() and (np.abs(arr) < 1e6).all())
                        if finite:
                            cache[name] = arr.copy()
                        else:
                            bad = True
                    if (not bad and not force_restore) or not cache:
                        continue
                    for name, arr in cache.items():
                        attr = prim.GetAttribute(name)
                        if name == 'orientations':
                            value = lazy.pxr.Vt.QuathArray.FromNumpy(arr)
                        else:
                            value = lazy.pxr.Vt.Vec3fArray(arr.tolist())
                        attr.Set(value)
                    if bad:
                        record['finite_guard_recoveries'] += 1
                        record.setdefault('finite_guard_recovery_usd_paths', []).append(path)
                    else:
                        record['finite_guard_render_resets'] = record.get('finite_guard_render_resets', 0) + 1
            except Exception as exc:
                record['finite_guard_failures'] += 1
                record.setdefault('finite_guard_errors', []).append('usd:' + type(exc).__name__)

        def render_water_guard():
            finite_usd_water_guard(force_restore=True)
            if not sim.currently_stepping:
                try: synchronize()
                except NameError: pass
        sim._finite_usd_water_guard = render_water_guard

        def attach_finite_guard():
            current = og.sim
            if getattr(current, '_finite_guard_attached_after_env', False):
                return
            post = current._on_post_physics_step
            @wraps(post)
            def attached_post(*args, **kwargs):
                nonlocal finite_guard_counter
                result = post(*args, **kwargs)
                finite_guard_counter += 1
                if finite_guard_enabled and finite_guard_counter % finite_guard_interval == 0:
                    finite_particle_guard()
                return result
            current._on_post_physics_step = attached_post
            def subscribed_guard(_event):
                nonlocal finite_guard_counter
                finite_guard_counter += 1
                if finite_guard_enabled and finite_guard_counter % finite_guard_interval == 0:
                    finite_particle_guard()
            interface = current._physics_context._physx_interface
            current._finite_guard_subscription_after_env = interface.subscribe_physics_on_step_events(subscribed_guard, False, 0)
            current._finite_guard_attached_after_env = True
            record['finite_guard_attached_after_env'] = True
            record['finite_guard_subscription_after_env'] = True
        global _LAST_ATTACH_GUARD
        _LAST_ATTACH_GUARD = attach_finite_guard
        def synchronize():
            nonlocal synchronizing
            if restore_depth or synchronizing:
                record['deferred_syncs'] = record.get('deferred_syncs', 0) + 1
                return
            if not sim.currently_stepping:
                synchronizing = True
                try:
                    if sim.is_playing():
                        sim._in_sim_lifecycle += 1
                        try:
                            sim.psi.flush_changes()
                            physx.update_transformations(False, True, False, False)
                        finally:
                            sim._in_sim_lifecycle -= 1
                    sim.usdrt_stage.SynchronizeToFabric()
                    sim.fabric_hierarchy.update_world_xforms()
                    record['geometry_syncs'] += 1
                finally:
                    synchronizing = False
        @contextmanager
        def restore_transaction():
            nonlocal restore_depth
            restore_depth += 1
            try:
                yield
            finally:
                restore_depth -= 1
                if restore_depth == 0:
                    record['state_restore_batches'] = record.get('state_restore_batches', 0) + 1
                    synchronize()
        from omnigibson.utils.python_utils import Serializable
        load_state = Serializable.load_state
        @wraps(load_state)
        def atomic_load_state(obj, *args, **kwargs):
            with restore_transaction():
                result = load_state(obj, *args, **kwargs)
                # State restoration can recreate the native water instancer;
                # immediately reapply the latest finite USD snapshot before
                # the next render or physics boundary.
                try:
                    finite_usd_water_guard(force_restore=True)
                except Exception:
                    pass
                return result
        Serializable.load_state = atomic_load_state
        sim.state_restore_transaction = restore_transaction
        sim.sync_physx_to_fabric = synchronize
        step = sim.step_physics
        @wraps(step)
        def physics(*args, **kwargs):
            nonlocal finite_guard_counter
            result = step(*args, **kwargs)
            finite_guard_counter += 1
            if finite_guard_enabled and finite_guard_counter % finite_guard_interval == 0:
                finite_particle_guard()
            synchronize()
            return result
        sim.step_physics = physics
        non_physics = sim._non_physics_step
        @wraps(non_physics)
        def update(*args, **kwargs):
            nonlocal finite_guard_counter
            synchronize()
            result = non_physics(*args, **kwargs)
            finite_guard_counter += 1
            if finite_guard_enabled and finite_guard_counter % finite_guard_interval == 0:
                finite_particle_guard()
            return result
        sim._non_physics_step = update
        public_step = sim.step
        @wraps(public_step)
        def public_step_guard(*args, **kwargs):
            nonlocal finite_guard_counter
            result = public_step(*args, **kwargs)
            finite_guard_counter += 1
            if finite_guard_enabled and finite_guard_counter % finite_guard_interval == 0:
                finite_particle_guard()
            return result
        sim.step = public_step_guard
        context = sim._sim_context
        context_step = context.step
        @wraps(context_step)
        def context_step_guard(*args, **kwargs):
            nonlocal finite_guard_counter
            result = context_step(*args, **kwargs)
            finite_guard_counter += 1
            if finite_guard_enabled and finite_guard_counter % finite_guard_interval == 0:
                finite_particle_guard()
            return result
        context.step = context_step_guard
        post_physics = sim._on_post_physics_step
        @wraps(post_physics)
        def post_physics_guard(*args, **kwargs):
            nonlocal finite_guard_counter
            result = post_physics(*args, **kwargs)
            finite_guard_counter += 1
            if finite_guard_enabled and finite_guard_counter % finite_guard_interval == 0:
                finite_particle_guard()
            return result
        sim._on_post_physics_step = post_physics_guard
        original_environment = getattr(og, 'Environment', None)
        if original_environment is not None and not getattr(original_environment, '_finite_guard_wrapped', False):
            @wraps(original_environment)
            def guarded_environment(*args, **kwargs):
                environment = original_environment(*args, **kwargs)
                attach_finite_guard()
                return environment
            guarded_environment._finite_guard_wrapped = True
            og.Environment = guarded_environment
        record.update(status='configured', gpu_dynamics=bool(gm.USE_GPU_DYNAMICS),
                      particle_quaternion_storage='native_Vt_QuathArray_xyzw_v1',
                      physics_solver_type=sim.get_physics_context().get_solver_type(),
                      tensor_pipeline_device=str(sim.device), controller_tensor_device=str(sim.device),
                      physx_fabric_output=False,
                      update_to_usd=True, update_particles_to_usd=True,
                      suppress_readback=False, physics_steps_added=0)
        synchronize()
        save()
        return value
    launch._manipulation_particle_readback = True
    og.launch = launch
    atexit.register(save)
    return record
