"""Opt-in device admission at native quaternion-operation boundaries.

Scalar CPU tensor constants can work in eager quaternion expressions but fail
when compiled into a CUDA kernel. Move only mixed-device input tensors to the
existing CUDA input's device before calling the unchanged simulator operation.
"""
from functools import wraps
import json
from pathlib import Path

PROTOCOL = 'native_gpu_pose_and_geometry_v5'


def install(record_path):
    import torch
    from omnigibson.utils import transform_utils as transforms

    from contextvars import ContextVar
    ray_cpu = ContextVar('native_cpu_ray_sampler', default=False)

    path = Path(record_path)
    patterns, originals = {}, {}
    path.write_text(json.dumps({'protocol': PROTOCOL, 'patterns': []}, indent=2) + '\n')

    def wrap(name, original):
        @wraps(original)
        def aligned(*args, **kwargs):
            tensors = [value for value in (*args, *kwargs.values()) if isinstance(value, torch.Tensor)]
            devices = {value.device for value in tensors}
            if len(devices) > 1:
                cuda = {device for device in devices if device.type == 'cuda'}
                if len(cuda) != 1:
                    raise ValueError('Native quaternion inputs span multiple CUDA devices')
                target = next(iter(cuda))
                key = (name, tuple(str(value.device) for value in tensors))
                if key not in patterns:
                    patterns[key] = dict(operation=name, original_devices=list(key[1]),
                                         execution_device=str(target), values_unchanged=True)
                    path.write_text(json.dumps({'protocol': PROTOCOL,
                                                'patterns': list(patterns.values())}, indent=2) + '\n')
                args = tuple(value.to(target) if isinstance(value, torch.Tensor) else value for value in args)
                kwargs = {key: value.to(target) if isinstance(value, torch.Tensor) else value
                          for key, value in kwargs.items()}
            return original(*args, **kwargs)
        return aligned

    for name in ('quat_multiply', 'quat_apply'):
        original = getattr(transforms, name)
        originals[name] = original
        setattr(transforms, name, wrap(name, original))
    from omnigibson.objects.usd_object import USDObject
    from omnigibson.utils import object_state_utils

    def move_output(value, device):
        if isinstance(value, torch.Tensor):
            return value.to(device)
        if isinstance(value, tuple):
            return tuple(move_output(item, device) for item in value)
        if isinstance(value, list):
            return [move_output(item, device) for item in value]
        return value

    def record_boundary(name, device):
        key = (name, str(device))
        if key not in patterns:
            patterns[key] = dict(operation=name, execution_device=str(device), values_unchanged=True)
            path.write_text(json.dumps({'protocol': PROTOCOL,
                                        'patterns': list(patterns.values())}, indent=2) + '\n')

    def ensure_pose_sync():
        import omnigibson as og
        sim = og.sim
        if sim is None or not hasattr(sim, 'fabric_hierarchy') or getattr(sim, '_manipulation_pose_sync', False):
            return
        sync = sim.sync_physx_to_fabric
        step = sim.step_physics
        @wraps(sync)
        def synchronized(*args, **kwargs):
            value = sync(*args, **kwargs)
            if not sim.currently_stepping:
                sim.fabric_hierarchy.update_world_xforms()
            return value
        @wraps(step)
        def synchronized_step(*args, **kwargs):
            value = step(*args, **kwargs)
            synchronized()
            return value
        sim.sync_physx_to_fabric = synchronized
        sim.step_physics = synchronized_step
        sim._manipulation_pose_sync = True
        synchronized()
        record_boundary('sync_fabric_hierarchy_after_private_physics_step', 'native')

    bbox = USDObject.get_base_aligned_bbox
    originals['USDObject.get_base_aligned_bbox'] = bbox
    @wraps(bbox)
    def device_bbox(obj, *args, **kwargs):
        ensure_pose_sync()
        result = bbox(obj, *args, **kwargs)
        device = torch.device('cpu') if ray_cpu.get() else obj.get_position_orientation()[0].device
        record_boundary('USDObject.get_base_aligned_bbox', device)
        return move_output(result, device)
    USDObject.get_base_aligned_bbox = device_bbox

    sample = object_state_utils.sample_cuboid_for_predicate
    originals['sample_cuboid_for_predicate'] = sample
    @wraps(sample)
    def device_sample(predicate, on_obj, bbox_extent):
        token = ray_cpu.set(True)
        try:
            result = sample(predicate, on_obj, bbox_extent.cpu())
        finally:
            ray_cpu.reset(token)
        record_boundary('sample_cuboid_for_predicate', bbox_extent.device)
        return move_output(result, bbox_extent.device)
    object_state_utils.sample_cuboid_for_predicate = device_sample
    # The generic native path (robots and cloth) reads AABB instead of
    # USDObject.get_base_aligned_bbox. Align only the moving object's read
    # within sampling; preserve the cached state and every numeric value.
    from contextvars import ContextVar
    from omnigibson.object_states import AABB
    from omnigibson.object_states import on_top, under
    sampling_object = ContextVar('native_sampling_object', default=None)
    aabb_read = AABB.get_value
    @wraps(aabb_read)
    def sampling_aabb(state, *args, **kwargs):
        value = aabb_read(state, *args, **kwargs)
        if state.obj is sampling_object.get():
            device = state.obj.get_position_orientation()[0].device
            record_boundary('sampling_AABB.get_value', device)
            return move_output(value, device)
        return value
    AABB.get_value = sampling_aabb
    kinematics = object_state_utils.sample_kinematics
    @wraps(kinematics)
    def device_kinematics(predicate, objA, objB, *args, **kwargs):
        token = sampling_object.set(objA)
        try:
            return kinematics(predicate, objA, objB, *args, **kwargs)
        finally:
            sampling_object.reset(token)
    object_state_utils.sample_kinematics = device_kinematics
    for module in (on_top, under):
        if module.sample_kinematics is not kinematics:
            raise RuntimeError('Unexpected native kinematics adapter already installed')
        module.sample_kinematics = device_kinematics
    from omnigibson.utils.bddl_utils import BDDLSampler
    states = BDDLSampler.sample_states
    originals['BDDLSampler.sample_states'] = states
    @wraps(states)
    def synchronized_states(*args, **kwargs):
        ensure_pose_sync()
        return states(*args, **kwargs)
    BDDLSampler.sample_states = synchronized_states
    ensure_pose_sync()
    originals['ensure_pose_sync'] = ensure_pose_sync
    return originals
