"""Explicit upper-body joint poses; base and grippers are never recovery targets."""
from copy import deepcopy

from .contracts import SkillError, object_schema, validate
from .oracle_contracts import checked_pose, vector


POSTURE_JOINT_NAMES = tuple(
    [f'torso_joint{i}' for i in range(1, 5)]
    + [f'{arm}_arm_joint{i}' for arm in ('left', 'right') for i in range(1, 8)])
POSTURE_TOLERANCE_RAD = .05


def goal_schema():
    schema = object_schema({'position_m': vector(3), 'quaternion_xyzw': vector(4),
        'joint_positions_rad': object_schema({name: {'type': 'number'} for name in POSTURE_JOINT_NAMES})})
    schema['required'] = []
    schema['description'] = ('Supply exactly position_m+quaternion_xyzw for a single EEF, '
        'or exactly joint_positions_rad for a whole-upper-body pose (arm=both, hold, no contact target). '
        'The latter specifies all four torso and both seven-joint arms; base and grippers stay unchanged.')
    return schema


def checked_goal(operation):
    goal = operation['goal']
    validate(goal, goal_schema())
    if set(goal) == {'joint_positions_rad'}:
        if (operation['arm'] != 'both' or operation['gripper'] != 'hold'
                or operation['contact_target'] is not None):
            raise SkillError('invalid_arguments', 'Whole-body joint poses require arm=both, gripper=hold and contact_target=null')
        return deepcopy(goal)
    if set(goal) != {'position_m', 'quaternion_xyzw'} or operation['arm'] not in ('left', 'right'):
        raise SkillError('invalid_arguments', 'Use one complete EEF goal or one complete whole-body joint goal')
    pose = checked_pose(dict(goal, gripper=operation['gripper']))
    return {key: pose[key] for key in goal}
