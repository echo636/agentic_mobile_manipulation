"""Scoped device-default compatibility for the pinned CuRobo constructor."""
from contextlib import contextmanager
import inspect


@contextmanager
def trajectory_evaluator_device(config_class,tensor_args):
    """Supply omitted tensor_args; retain explicit args and all numeric settings.

    Pinned MotionGenConfig omits this argument when building TrajEvaluatorConfig,
    whose Python default was constructed on cuda:0. No upstream files are edited.
    Simulator startup is serialized; restore the staticmethod even on failure.
    """
    descriptor=vars(config_class)['from_basic']
    original=config_class.from_basic;signature=inspect.signature(original)
    def assigned(*args,**kwargs):
        bound=signature.bind(*args,**kwargs)
        if 'tensor_args' not in bound.arguments:bound.arguments['tensor_args']=tensor_args
        return original(*bound.args,**bound.kwargs)
    config_class.from_basic=staticmethod(assigned)
    try:yield
    finally:config_class.from_basic=descriptor
