"""Instance-scoped call-signature compatibility for pinned symbolic navigation."""
from functools import wraps
import inspect


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
