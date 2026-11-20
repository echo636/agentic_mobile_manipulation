"""User-saved R1Pro whole-body recovery reference from measured native posture.

Calibrated torso and BOTH arms, independent of which hand currently holds an
object. Base/grippers are excluded. The host plans a fresh joint-space path;
these constants are not simulator-state reset commands or an old trajectory.
"""

REFERENCE_JOINT_POSITIONS_RAD = {
    "torso_joint1": -0.43985891342163086,
    "torso_joint2": 0.48001566529273987,
    "torso_joint3": 0.08119270950555801,
    "torso_joint4": -0.8062744736671448,
    "left_arm_joint1": -2.377708187850658e-05,
    "left_arm_joint2": 7.78791363700293e-05,
    "left_arm_joint3": 0.000822790723759681,
    "left_arm_joint4": 1.3150044651411008e-05,
    "left_arm_joint5": -0.0007134482730180025,
    "left_arm_joint6": -1.2009090823994484e-05,
    "left_arm_joint7": 6.6996767600358e-06,
    "right_arm_joint1": 0.3201662302017212,
    "right_arm_joint2": -0.3651871979236603,
    "right_arm_joint3": -0.3483026921749115,
    "right_arm_joint4": -2.0941901206970215,
    "right_arm_joint5": -0.43559470772743225,
    "right_arm_joint6": 0.32150599360466003,
    "right_arm_joint7": 1.049560308456421
}


def generate(task, oracle, parameters):
    if oracle.get('frame') != 'robot_base':
        raise ValueError('Use current robot_base state')
    robot = oracle['robot']
    if (len(robot['trunk']['joint_positions_rad']) != 4 or
            any(len(robot['arms'][arm]['joint_positions_rad']) != 7 for arm in ('left', 'right'))):
        raise ValueError('Recovery reference requires the R1Pro torso and both seven-joint arms')
    if set(parameters) - {'control_steps'}:
        raise ValueError('This is a fixed whole-body reference; only control_steps may be supplied')
    phase = dict(name='recover_body', arm='both', frame='robot_base',
        goal={'joint_positions_rad': dict(REFERENCE_JOINT_POSITIONS_RAD)},
        gripper='hold', contact_target=None)
    if 'control_steps' in parameters:
        steps = parameters['control_steps']
        if type(steps) is not int or steps < 1:
            raise ValueError('control_steps must be a positive integer duration estimate')
        phase['control_steps'] = steps
    return {'phases': [phase]}
