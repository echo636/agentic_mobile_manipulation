"""Scoped device and collision-cache compatibility for pinned CuRobo."""
from contextlib import contextmanager
import inspect


@contextmanager
def trajectory_evaluator_device(config_class,tensor_args,method='from_basic'):
    """Supply omitted tensor_args; retain explicit args and all numeric settings.

    Pinned MotionGenConfig omits this argument when building TrajEvaluatorConfig,
    whose Python default was constructed on cuda:0. No upstream files are edited.
    Simulator startup is serialized; restore the staticmethod even on failure.
    """
    descriptor=vars(config_class)[method]
    # ArmBaseConfig.from_dict is also inherited by ArmReacherConfig: preserve
    # classmethod binding so injecting a device never changes the result type.
    original=descriptor.__func__;signature=inspect.signature(original)
    def assigned(*args,**kwargs):
        bound=signature.bind(*args,**kwargs)
        if 'tensor_args' not in bound.arguments:bound.arguments['tensor_args']=tensor_args
        return original(*bound.args,**bound.kwargs)
    setattr(config_class,method,type(descriptor)(assigned))
    try:yield
    finally:setattr(config_class,method,descriptor)


def scene_mesh_cache_size(robot, floor_plane):
    """Count the native update_obstacles mesh set before CUDA graph warmup.

    Include the floor and every collision mesh of non-robot, non-visual-only
    objects. Omitting the optional ignore_objects filter makes this a bound for
    the current scene, without extracting geometry or changing collision data.
    """
    scene_meshes = sum(len(link.collision_meshes)
                       for obj in robot.scene.objects
                       if obj != robot and not obj.visual_only
                       for link in obj.links.values())
    floor_meshes = int(floor_plane is not None)
    count = scene_meshes + floor_meshes
    capacity = 1 << (max(2048, count) - 1).bit_length()
    return {'scene_collision_meshes': scene_meshes, 'floor_meshes': floor_meshes,
            'initial_mesh_count': count, 'capacity': capacity,
            'count_rule': 'native_update_obstacles_without_ignore_objects',
            'allocation_timing': 'before_motion_generator_cuda_graph_warmup'}


@contextmanager
def collision_mesh_cache_capacity(curobo_module, capacity):
    """Override only the constructor factory's mesh capacity, then restore it.

    Pinned CuRoboMotionGenerator hardcodes 2048 slots before capturing graphs.
    Its later mesh-cache resize replaces graph-referenced tensors. Reserve the
    scene capacity up front; keep device, graph settings and geometry unchanged.
    """
    original = curobo_module.create_world_mesh_collision
    signature = inspect.signature(original)

    def assigned(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        requested = bound.arguments.get('mesh_cache_size',
                                       signature.parameters['mesh_cache_size'].default)
        bound.arguments['mesh_cache_size'] = max(requested, capacity)
        return original(*bound.args, **bound.kwargs)

    curobo_module.create_world_mesh_collision = assigned
    try:
        yield
    finally:
        curobo_module.create_world_mesh_collision = original


def planner_cache_snapshot(motion_generator):
    """Private tensor metadata only: no CUDA kernel, synchronization or readback."""
    embodiments = {}
    caches = {}
    for embodiment, planner in motion_generator.mg.items():
        checker = planner.world_coll_checker
        cache_id = str(id(checker))
        if cache_id not in caches:
            caches[cache_id] = {
                'device': str(checker.tensor_args.device),
                'tensors': [
                    {'data_ptr': tensor.data_ptr(), 'shape': list(tensor.shape),
                     'device': str(tensor.device), 'dtype': str(tensor.dtype)}
                    for tensor in checker._mesh_tensor_list],
            }
        solvers = {}
        for name in ('ik_solver', 'trajopt_solver', 'finetune_trajopt_solver'):
            solver = getattr(planner, name)
            solvers[name] = {
                'device': str(solver.tensor_args.device),
                'optimizers': [
                    {'type': type(opt).__name__, 'device': str(opt.tensor_args.device),
                     'use_cuda_graph': bool(opt.use_cuda_graph)}
                    for opt in solver.solver.optimizers],
            }
        embodiments[getattr(embodiment, 'name', str(embodiment))] = {
            'device': str(planner.tensor_args.device), 'cache_id': cache_id,
            'solvers': solvers,
        }
    return {'shared_caches': caches, 'embodiments': embodiments}
