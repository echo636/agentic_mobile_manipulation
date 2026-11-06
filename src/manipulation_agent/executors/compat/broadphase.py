"""Use native MBP to avoid the reproduced GPU broadphase fault on object replacement.

GPU dynamics and particle simulation stay enabled. The environment still owns
launch parameters; only collision-pair discovery changes before scene loading.
"""
from contextlib import contextmanager


@contextmanager
def native_mbp_broadphase(og):
    original = og.launch
    record = {'requested': 'MBP', 'gpu_dynamics_changed': False}

    def launch(*args, **kwargs):
        result = original(*args, **kwargs)
        with og.sim.editing_usd():
            og.sim._physics_context.set_broadphase_type('MBP')
        record.update(actual=og.sim._physics_context.get_broadphase_type(),
                      gpu_dynamics=og.sim._physics_context.is_gpu_dynamics_enabled())
        return result

    og.launch = launch
    try:
        yield record
    finally:
        og.launch = original
