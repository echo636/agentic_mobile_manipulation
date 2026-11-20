"""At most one measured trunk move; no retry, navigation, preparation or release."""
import math

from .params import (ALREADY_SATISFIED_JOINT_ERROR_RAD,
                     JOINT_LIMIT_MARGIN_RAD, LIMIT_SIGNATURE_TOLERANCE_RAD,
                     MAX_JOINT_ERROR_RAD, MAX_UPPER_BODY_TILT_DEG,
                     R1PRO_LOWER_RAD, R1PRO_UPPER_RAD)


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _mapping(ctx, value, name):
    ctx.require(isinstance(value, dict), name + ' must be available')
    return value


def _vector(ctx, value, length, name):
    ctx.require(isinstance(value, list) and len(value) == length and
                all(_finite(x) for x in value), name + ' must contain finite numbers')
    return value


def _state(ctx):
    observation = ctx.observation
    ctx.require(observation.get('observation_mode') == 'oracle_3d_waypoints',
                'Requires the R1Pro Oracle observation contract')
    oracle = _mapping(ctx, observation.get('oracle_3d'), 'Oracle state')
    robot = _mapping(ctx, oracle.get('robot'), 'Robot state')
    ctx.require(robot.get('grasping_mode') == 'assisted',
                'This draft checks empty hands through native assisted retention only')
    trunk = _mapping(ctx, robot.get('trunk'), 'Trunk state')
    actual = _vector(ctx, trunk.get('joint_positions_rad'), 4, 'Measured trunk joints')
    limits = _mapping(ctx, trunk.get('joint_limits_rad'), 'Measured trunk limits')
    lower = _vector(ctx, limits.get('lower'), 4, 'Lower joint limits')
    upper = _vector(ctx, limits.get('upper'), 4, 'Upper joint limits')
    ctx.require(all(lo < hi for lo, hi in zip(lower, upper)), 'Invalid joint limit intervals')
    ctx.require(all(abs(a-b) <= LIMIT_SIGNATURE_TOLERANCE_RAD for a, b in
                    zip(lower + upper, R1PRO_LOWER_RAD + R1PRO_UPPER_RAD)),
                'Joint limit signature differs from the calibrated R1Pro asset')
    posture = _mapping(ctx, trunk.get('body_posture'), 'Measured body posture')
    ctx.require(posture.get('link') == 'torso_link4' and
                posture.get('vertical_axis_local') == [0., 0., 1.],
                'Requires the calibrated torso_link4 +Z posture convention')
    tilt = posture.get('upper_body_tilt_deg')
    height = posture.get('upper_body_height_above_base_m')
    ctx.require(_finite(tilt) and 0 <= tilt <= 180, 'Measured upper-body tilt unavailable')
    ctx.require(_finite(height), 'Measured upper-body height unavailable')
    return robot, actual, lower, upper, tilt, height


def _empty_hands(ctx, robot):
    arms = _mapping(ctx, robot.get('arms'), 'Both arms')
    contact_availability = {}
    for name in ('left', 'right'):
        arm = _mapping(ctx, arms.get(name), name + ' arm')
        native = _mapping(ctx, arm.get('native_grasp'), name + ' native retention')
        ctx.require('held_object' in arm and arm['held_object'] is None and
                    native.get('mode') == 'assisted' and 'object_id' in native and
                    native['object_id'] is None, 'Both hands must be empty before trunk motion')
        contacts = arm.get('contacts')
        available = isinstance(contacts, dict) and contacts.get('available') is True
        contact_availability[name] = available
        if available:
            ctx.require(contacts.get('pairs') == [] and contacts.get('finger_count') == 0,
                        name + ' hand has contact or incomplete contact evidence; prepare first')
    return contact_availability


def main(ctx, q1_rad, q2_rad, yaw_rad, prepared_for_trunk_motion,
         expected_height_interval_m):
    ctx.require(prepared_for_trunk_motion is True,
                'Caller must first prepare both arms and inspect the trunk/arm swept clearance')
    ctx.require(all(_finite(x) for x in (q1_rad, q2_rad, yaw_rad)),
                'q1, q2 and yaw must be finite radians')
    # Joint axes are +Y, +Y, -Y, +Z: q3 has the sign of q1+q2, not a fixed sign.
    target = [q1_rad, q2_rad, q1_rad + q2_rad, yaw_rad]
    ctx.require(all(_finite(x) for x in target), 'Derived q3 must be finite')
    robot, actual, lower, upper, tilt, height = _state(ctx)
    contacts_before = _empty_hands(ctx, robot)
    margins = [min(q-lo, hi-q) for q, lo, hi in zip(target, lower, upper)]
    ctx.require(all(margin >= JOINT_LIMIT_MARGIN_RAD for margin in margins),
                'Target violates observed joint limits or the draft joint-limit margin')
    if expected_height_interval_m is not None:
        interval = _vector(ctx, expected_height_interval_m, 2, 'Expected height interval')
        ctx.require(0 <= interval[0] < interval[1], 'Expected height interval must be ordered and nonnegative')

    already_satisfied = (max(abs(a-b) for a, b in zip(actual, target)) <=
                         ALREADY_SATISFIED_JOINT_ERROR_RAD and
                         tilt <= MAX_UPPER_BODY_TILT_DEG and
                         (expected_height_interval_m is None or
                          expected_height_interval_m[0] <= height <= expected_height_interval_m[1]))
    contacts_after = contacts_before
    if not already_satisfied:
        # SkillContext supplies revision and raises on tool failure. At most one
        # physical call; no extra wait or recovery side effects follow failure.
        reply = ctx.call('move_joints', group='trunk', positions_rad=target)
        ctx.require(reply.get('execution', {}).get('reached') is True,
                    'Trunk move did not reach its target')
        robot, actual, _, _, tilt, height = _state(ctx)
        contacts_after = _empty_hands(ctx, robot)
    error = max(abs(a-b) for a, b in zip(actual, target))
    ctx.require(error <= MAX_JOINT_ERROR_RAD,
                'Measured trunk joint error exceeds draft acceptance tolerance')
    ctx.require(tilt <= MAX_UPPER_BODY_TILT_DEG,
                'Measured world-vertical upper-body tilt exceeds draft upright tolerance')
    if expected_height_interval_m is not None:
        ctx.require(expected_height_interval_m[0] <= height <= expected_height_interval_m[1],
                    'Measured upper-body height is outside the caller-selected interval')
    return {'ok': True, 'why': '', 'measured': {
        'already_satisfied': already_satisfied,
        'target_trunk_rad': target, 'actual_trunk_rad': actual,
        'max_joint_error_rad': error, 'upper_body_tilt_deg': tilt,
        'upper_body_height_above_base_m': height,
        'target_joint_limit_margins_rad': margins,
        'empty_assisted_hands': True, 'finger_contacts_available_before': contacts_before,
        'finger_contacts_available_after': contacts_after,
        'caller_prepared_clearance': True, 'swept_collision_safety_verified': False,
        'acceptance': {'joint_limit_margin_rad': JOINT_LIMIT_MARGIN_RAD,
                       'already_satisfied_joint_error_rad': ALREADY_SATISFIED_JOINT_ERROR_RAD,
                       'max_joint_error_rad': MAX_JOINT_ERROR_RAD,
                       'max_upper_body_tilt_deg': MAX_UPPER_BODY_TILT_DEG}}}
