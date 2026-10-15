"""Prepare the missing native navigation dependencies on one primitive instance."""
import hashlib
import inspect
from pathlib import Path

from .curobo_compat import (
    collision_mesh_cache_capacity, planner_cache_snapshot, scene_mesh_cache_size,
    trajectory_evaluator_device,
)
from .holonomic_navigation_frames import install_instance_adapter
from .symbolic_compat import adapt_symbolic_navigation_signature

NAVIGATION_EMBODIMENTS = ('DEFAULT', 'ARM')


def initialize_official_navigation(primitives, floor_plane):
    import torch
    from omnigibson.action_primitives import curobo as native_curobo
    from omnigibson.action_primitives.curobo import CuRoboEmbodimentSelection
    from omnigibson.action_primitives.starter_semantic_action_primitives import m
    from curobo.wrap.reacher.evaluator import TrajEvaluatorConfig
    from curobo.rollout.arm_base import ArmBaseConfig
    from curobo.types.base import TensorDeviceType

    # Symbolic uses DEFAULT collision checks and ARM reachability. Its endpoint
    # teleports to a validated pose, never calls the BASE trajectory planner.
    required = tuple(getattr(CuRoboEmbodimentSelection, name) for name in NAVIGATION_EMBODIMENTS)
    configs = primitives.robot.curobo_path
    if not all(k in configs for k in required):
        raise RuntimeError('Official navigation requires native DEFAULT and ARM configurations')
    device = f'cuda:{torch.cuda.current_device()}'
    tensor_args = TensorDeviceType(device=torch.device(device))
    allocation = scene_mesh_cache_size(primitives.robot, floor_plane)
    with trajectory_evaluator_device(TrajEvaluatorConfig, tensor_args), \
            trajectory_evaluator_device(ArmBaseConfig, tensor_args, 'from_dict'), \
            collision_mesh_cache_capacity(native_curobo, allocation['capacity']):
        motion = native_curobo.CuRoboMotionGenerator(
            robot=primitives.robot, robot_cfg_path={k: configs[k] for k in required},
            device=device, batch_size=primitives._curobo_batch_size,
            collision_activation_distance=m.DEFAULT_COLLISION_ACTIVATION_DISTANCE)
    if not all(k in motion.mg for k in required):
        raise RuntimeError('Official navigation planner is missing a required embodiment')
    primitives._motion_generator = motion
    signature = adapt_symbolic_navigation_signature(primitives)
    frame = install_instance_adapter(primitives)
    source = Path(inspect.getsourcefile(native_curobo.CuRoboMotionGenerator))
    return {
        'configuration': 'official_navigation_compat_20261015',
        'planner': 'native_CuRoboMotionGenerator', 'device': device,
        'embodiments': [k.name for k in required], 'allocation': allocation,
        'signature': signature, 'frames': frame,
        'planner_snapshot': planner_cache_snapshot(motion),
        'native_planner_source': str(source),
        'native_planner_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'candidate_sampling': 'unchanged_native', 'collision_and_ik': 'native',
        'endpoint': 'native_symbolic_pose_setter_then_settle',
        'custom_gt_navigation': False, 'custom_fallback': False,
    }
