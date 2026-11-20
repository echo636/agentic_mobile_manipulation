from manipulation_agent.program_geometry import add, scale, unit, pose, hinge_path, object_by_id, retained
from .params import ARC_RAD, ARC_POINT_RAD, JOINT_TOLERANCE_RAD, MAX_OPENING_RAD, MAX_SEGMENTS, RETREAT_M, HOLD_STEPS


def main(ctx, arm, target_object_id, target_link, joint_id, goal_position_rad,
         minimum_released_position_rad, retreat_direction_base, allow_trunk=False):
    # This learned microwave procedure opens in the positive joint direction.
    direction = unit(retreat_direction_base)
    obs = ctx.observe()
    def state(observation):
        obj = object_by_id(observation, target_object_id)
        joint = next((j for j in obj['joints'] if j['id'] == joint_id), None)
        ctx.require(joint is not None and joint.get('type') == 'revolute' and
                    joint.get('child_link') == target_link and joint.get('frame') == 'robot_base',
                    'Need a directly observed revolute joint driving the retained link')
        return obj, joint
    obj, joint = state(obs)
    start = joint['position']
    ctx.require(retained(obs, arm, target_object_id, target_link), 'Need assisted retention on the intended movable link')
    ctx.require(joint['lower_limit'] <= minimum_released_position_rad <= goal_position_rad <= joint['upper_limit'],
                'Requested positive opening and released threshold must lie within current limits')
    ctx.require(0 <= goal_position_rad-start <= MAX_OPENING_RAD, 'Opening outside this positive-arc skill domain')
    segments = []
    while goal_position_rad-joint['position'] > JOINT_TOLERANCE_RAD:
        ctx.require(len(segments) < MAX_SEGMENTS, 'Insufficient progress; hand control back for recovery')
        ctx.require(retained(obs, arm, target_object_id, target_link), 'Movable-link retention was lost')
        angle = min(ARC_RAD, goal_position_rad-joint['position'])
        eef = obs['oracle_3d']['robot']['arms'][arm]['eef_pose']
        path = hinge_path(eef, joint['axis_origin_m'], joint['axis_direction'], angle, ARC_POINT_RAD)
        result = ctx.call('rotate', arm=arm, frame='robot_base', allow_trunk=allow_trunk,
                          target_object_id=target_object_id, target_link=target_link, joint_id=joint_id,
                          angle_rad=angle, motion_waypoints=path, release_after=False, settle_steps=5)
        ex = result['execution']
        ctx.require(ex.get('verified') is True and ex.get('actual_delta', 0) >= angle*.5,
                    'Measured hinge progress/retention was not verified')
        segments.append({'requested_rad': angle, 'measured_delta_rad': ex['actual_delta']})
        obs = ctx.observation;obj, joint = state(obs)
    ctx.call('release', arm=arm, frame='robot_base', allow_trunk=False,
             target_object_id=target_object_id, settle_steps=10)
    obs = ctx.observation
    eef = obs['oracle_3d']['robot']['arms'][arm]['eef_pose']
    ctx.call('execute_waypoints', arm=arm, frame='robot_base',
             waypoints=[pose(add(eef['position_m'], scale(direction, RETREAT_M)), eef['quaternion_xyzw'])])
    before = ctx.observation;_, j0 = state(before)
    ctx.require(j0['position'] >= minimum_released_position_rad, 'Door relaxed below threshold after release/retreat')
    first_step = before['capture']['sim_step']
    ctx.call('wait', steps=HOLD_STEPS)
    obs = ctx.observation;_, j1 = state(obs)
    ctx.require(j1['position'] >= minimum_released_position_rad and obs['capture']['sim_step']-first_step == HOLD_STEPS,
                'Final opening or hold interval failed')
    ctx.require(all(h.get('held_object') != target_object_id for h in obs['oracle_3d']['robot']['arms'].values()),
                'Handle still retained after release')
    return {'ok': True, 'why': '', 'measured': {'initial_rad': start, 'final_rad': j1['position'],
            'released': True, 'segments': segments, 'hold_steps': HOLD_STEPS,
            'hold_endpoint_angles_rad': [j0['position'], j1['position']],
            'continuous_hold': 'requires independent per-control-step passive-trace review'}}
