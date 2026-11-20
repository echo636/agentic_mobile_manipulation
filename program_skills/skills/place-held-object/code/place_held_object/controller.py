import math
from manipulation_agent.program_geometry import add, sub, point, pose, linear_path, object_by_id, retained
from .params import SETTLE_STEPS, CLEARANCE_M, PATH_SPACING_M, MAX_TRANSFER_M


def main(ctx, arm, target_object_id, destination_object_id, release_position_in_destination_m,
         allow_trunk=False):
    obs = ctx.observe()
    obj, destination = [object_by_id(obs, ident) for ident in [target_object_id, destination_object_id]]
    ctx.require(target_object_id != destination_object_id, 'Source and destination must differ')
    ctx.require(retained(obs, arm, target_object_id), 'Need verified native assisted retention before transport')
    ctx.require(not obj['fixed_base'], 'Cannot transport a fixed object')
    hand = obs['oracle_3d']['robot']['arms'][arm]['eef_pose']
    start, orientation = hand['position_m'], hand['quaternion_xyzw']
    desired_object = point(destination['pose'], release_position_in_destination_m)
    ctx.require(math.dist(obj['pose']['position_m'], desired_object) <= MAX_TRANSFER_M,
                'Destination outside this local-transfer code domain; reposition before grasping')
    release = add(start, sub(desired_object, obj['pose']['position_m']))
    # Clearance includes the whole carried object, not just the EEF origin.
    bottom_offset = start[2] - obj['aabb']['min_m'][2]
    high = max(start[2], release[2], destination['aabb']['max_m'][2] + bottom_offset + CLEARANCE_M)
    above_source = [start[0], start[1], high]
    above_destination = [release[0], release[1], high]
    path = [pose(above_source, orientation)]
    path += linear_path(above_source, above_destination, orientation, PATH_SPACING_M)
    path += linear_path(above_destination, release, orientation, PATH_SPACING_M)
    ctx.require(len(path) <= 32, 'Placement path exceeds native operation capacity')
    reply = ctx.call('place', arm=arm, frame='robot_base', allow_trunk=allow_trunk,
                     target_object_id=target_object_id, destination_object_id=destination_object_id,
                     relation='inside', placement_waypoints=path,
                     retreat_waypoints=[pose(above_destination, orientation), pose(above_source, orientation)],
                     settle_steps=SETTLE_STEPS)
    evidence = reply['execution']
    ctx.require(evidence.get('relation_satisfied') is True and evidence.get('released') is True and
                evidence.get('reached') is True and evidence.get('stability', {}).get('stable') is True,
                'Inside, release, retreat and settling must each be verified')
    ctx.require(all(h.get('held_object') != target_object_id for h in ctx.observation['oracle_3d']['robot']['arms'].values()),
                'Target remains retained')
    return {'ok': True, 'why': '', 'measured': {'relation_satisfied': True, 'released': True,
            'retreat_reached': True, 'native_stability': evidence['stability'],
            'settle_steps': SETTLE_STEPS, 'stable_flag_window_steps': 10,
            'strict_60_step_stability': 'requires independent passive-trace review',
            'release_object_position_base_m': desired_object}}
