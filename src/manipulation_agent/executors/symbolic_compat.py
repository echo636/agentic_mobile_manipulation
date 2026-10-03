"""Instance-scoped call-signature compatibility for pinned symbolic navigation."""
from functools import wraps
from contextlib import contextmanager
import inspect
import time


@contextmanager
def trace_native_settling(primitives, record, interval=50):
    """Observe native settling without changing its actions or stop condition.

    All generator operations delegate to the original, including cancellation.
    Sparse diagnostic failures must not replace the primitive's actual result.
    """
    missing = object()
    previous = vars(primitives).get('_settle_robot', missing)
    original = primitives._settle_robot

    @wraps(original)
    def traced(*args, **kwargs):
        generated = original(*args, **kwargs)
        count = 0

        def emit(phase):
            try:
                record(phase, count)
            except Exception:
                pass

        class ObservedGenerator:
            def __iter__(self): return self

            def observed(self, action):
                nonlocal count
                count += 1
                if count % interval == 0:
                    emit('yielded')
                return action

            def __next__(self): return self.observed(next(generated))
            def send(self, value): return self.observed(generated.send(value))
            def throw(self, *error): return self.observed(generated.throw(*error))
            def close(self): return generated.close()

        phase = 'failed'
        emit('started')
        try:
            result = yield from ObservedGenerator()
            phase = 'returned'
            return result
        except GeneratorExit:
            phase = 'closed'
            raise
        finally:
            emit(phase)

    primitives._settle_robot = traced
    try:
        yield
    finally:
        if previous is missing:
            delattr(primitives, '_settle_robot')
        else:
            primitives._settle_robot = previous


@contextmanager
def native_planning_checkpoints(primitives, check_budget, record_phase):
    """Check the existing action deadline inside a non-yielding native sampler.

    A generator can evaluate many candidate poses before yielding one control
    action. Check at native collision and per-candidate IK boundaries, preserving
    all arguments, sampling settings and results. A single native/CUDA call is
    still cooperative: it cannot be interrupted safely while executing.
    """
    missing = object()
    restore = []

    def record(name, status, duration, result_summary=None):
        try:
            record_phase(name, status, duration, result_summary)
        except OSError:
            # Diagnostics must not hide the original native exception or the
            # deadline, for example during a transient NAS write failure.
            pass

    def wrap(original, name):
        @wraps(original)
        def checked(*args, **kwargs):
            check_budget()
            started = time.monotonic()
            record(name, 'started', None)
            try:
                result = original(*args, **kwargs)
            except Exception:
                record(name, 'failed', time.monotonic() - started)
                raise
            result_summary = None
            if name == '_target_in_reach_of_robot':
                result_summary = {'reachable': bool(result)}
            elif name == '_validate_poses':
                valid_mask = [bool(value) for value in result]
                result_summary = {'valid_mask': valid_mask,
                                  'candidate_count': len(valid_mask), 'valid_count': sum(valid_mask)}
            record(name, 'returned', time.monotonic() - started, result_summary)
            check_budget()
            return result
        return checked

    try:
        for instance, name in (
            (primitives, '_validate_poses'),
            (primitives, '_target_in_reach_of_robot'),
            (primitives._motion_generator, 'update_obstacles'),
        ):
            previous = vars(instance).get(name, missing)
            original = getattr(instance, name)
            restore.append((instance, name, previous))
            setattr(instance, name, wrap(original, name))
        yield
    finally:
        for instance, name, previous in reversed(restore):
            if previous is missing:
                delattr(instance, name)
            else:
                setattr(instance, name, previous)


def adapt_symbolic_navigation_signature(primitives):
    """Accept the inherited caller's obstacle flag without changing its endpoint.

    Starter._navigate_to_obj forwards skip_obstacle_update both to its candidate
    sampler and to _navigate_to_pose. The pinned Symbolic override accepts only
    pose_2d and performs set_position_orientation followed by _settle_robot; it
    does not update obstacles or plan a trajectory. Ignore the flag only at this
    endpoint and return the original bound method's generator unchanged. The
    sampler, its collision/IK checks, and other primitive instances are untouched.
    """
    original = primitives._navigate_to_pose
    parameters = inspect.signature(original).parameters
    if 'skip_obstacle_update' in parameters:
        return {'applied': False, 'reason': 'upstream_signature_already_compatible'}
    if tuple(parameters) != ('pose_2d',):
        raise RuntimeError('Unsupported symbolic navigation endpoint signature')

    @wraps(original)
    def navigate_to_pose(pose_2d, *, skip_obstacle_update=False):
        return original(pose_2d)

    primitives._navigate_to_pose = navigate_to_pose
    return {'applied': True, 'scope': 'primitive_instance_only',
            'ignored_endpoint_keyword': 'skip_obstacle_update',
            'delegate': 'upstream_bound_SymbolicSemanticActionPrimitives._navigate_to_pose',
            'endpoint_behavior': 'unchanged_pose_setter_and_settling_generator'}
