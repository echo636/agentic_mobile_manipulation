"""Scoped device-default compatibility for the pinned CuRobo constructor."""
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
