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

PROTOCOL = 'native_gpu_particles_usd_readback_v5'


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
                return load_state(obj, *args, **kwargs)
        Serializable.load_state = atomic_load_state
        sim.state_restore_transaction = restore_transaction
        sim.sync_physx_to_fabric = synchronize
        step = sim.step_physics
        @wraps(step)
        def physics(*args, **kwargs):
            result = step(*args, **kwargs)
            synchronize()
            return result
        sim.step_physics = physics
        non_physics = sim._non_physics_step
        @wraps(non_physics)
        def update(*args, **kwargs):
            synchronize()
            return non_physics(*args, **kwargs)
        sim._non_physics_step = update
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
