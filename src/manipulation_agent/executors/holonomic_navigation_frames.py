"""Frame-only compatibility for pinned holonomic navigation.

The pure geometry helpers use plain lists and preserve OG's intrinsic XYZ Euler
convention (Rx @ Ry @ Rz). The instance adapter imports simulator dependencies only
when explicitly installed. It preserves native sampling, candidate order, collision
checks, attached-object handling, ARM IK and symbolic endpoint settling.
"""
from __future__ import annotations
import math


def transpose(a):
    return [list(row) for row in zip(*a)]


def matmul(a,b):
    return [[sum(x*y for x,y in zip(row,col)) for col in zip(*b)] for row in a]


def rotate(a,v):
    return [sum(x*y for x,y in zip(row,v)) for row in a]


def intrinsic_matrix(angles):
    r,p,y=angles;cr,sr=math.cos(r),math.sin(r);cp,sp=math.cos(p),math.sin(p);cy,sy=math.cos(y),math.sin(y)
    return [[cp*cy,-cp*sy,sp],
            [cr*sy+sr*sp*cy,cr*cy-sr*sp*sy,-sr*cp],
            [sr*sy-cr*sp*cy,sr*cy+cr*sp*sy,cr*cp]]


def intrinsic_angles(matrix):
    pitch=math.asin(max(-1.0,min(1.0,matrix[0][2])))
    if abs(math.cos(pitch))>1e-8:
        return [math.atan2(-matrix[1][2],matrix[2][2]),pitch,math.atan2(-matrix[0][1],matrix[0][0])]
    sign=1.0 if pitch>0 else -1.0
    return [math.atan2(sign*matrix[1][0],matrix[1][1]),pitch,0.0]


def candidate_world_pose(pose_2d,current_position,current_rotation):
    """World x/y/yaw candidate retaining WORLD height, roll and pitch."""
    angles=intrinsic_angles(current_rotation);angles[2]=float(pose_2d[2])
    return [float(pose_2d[0]),float(pose_2d[1]),float(current_position[2])],intrinsic_matrix(angles)


def world_pose_to_base_joints(position,rotation,anchor_position,anchor_rotation):
    inverse=transpose(anchor_rotation)
    local_position=rotate(inverse,[p-a for p,a in zip(position,anchor_position)])
    local_rotation=matmul(inverse,rotation)
    return local_position+intrinsic_angles(local_rotation)


def base_joints_to_world_pose(joints,anchor_position,anchor_rotation):
    moved=rotate(anchor_rotation,joints[:3])
    return [a+b for a,b in zip(anchor_position,moved)],matmul(anchor_rotation,intrinsic_matrix(joints[3:]))


def install_instance_adapter(primitives):
    """Install the frame adapter on one Official primitive instance.

    This replaces two methods on this primitive instance only. Collision and IK
    checks remain native. DEFAULT locks local z/rx/ry: if transformed candidates
    require different locked values, evaluate them separately with matching locks.
    No obstacle is ignored, no pose is accepted without native validation.
    """
    import functools
    import hashlib
    import inspect
    from pathlib import Path
    import torch as th
    import omnigibson.utils.transform_utils as T
    robot=primitives.robot
    if not robot.is_holonomic_base:
        return {'applied':False,'reason':'non_holonomic_robot'}
    original_validate=primitives._validate_poses
    original_endpoint_pose=primitives._get_robot_pose_from_2d_pose
    source_path=Path(inspect.getsourcefile(original_validate))
    adapter_path=Path(__file__)
    def current_world():
        pos,quat=robot.get_position_orientation()
        return pos.detach().cpu().tolist(),T.quat2mat(quat).detach().cpu().tolist()
    def endpoint_pose(pose_2d):
        pos,rotation=candidate_world_pose(pose_2d,*current_world())
        return th.tensor(pos,dtype=th.float32),T.mat2quat(th.tensor(rotation,dtype=th.float32))
    @functools.wraps(original_validate)
    def validate(candidate_poses,eef_pose=None,plan_with_open_gripper=False,skip_obstacle_update=False):
        current_q=(primitives._get_joint_position_with_fingers_at_limit('upper')
                   if plan_with_open_gripper else robot.get_joint_positions())
        anchor_pos,anchor_quat=robot.root_link.get_position_orientation()
        anchor_pos=anchor_pos.detach().cpu().tolist()
        anchor_rotation=T.quat2mat(anchor_quat).detach().cpu().tolist()
        world_position,world_rotation=current_world()
        candidates=[]
        for pose in candidate_poses:
            desired=candidate_world_pose(pose,world_position,world_rotation)
            base=world_pose_to_base_joints(*desired,anchor_pos,anchor_rotation)
            q=current_q.clone();q[robot.base_idx]=th.tensor(base,dtype=q.dtype,device=q.device)
            candidates.append(q)
        candidate_q=th.stack(candidates)
        held=primitives._get_obj_in_hand()
        attached={robot.eef_link_names[primitives.arm]:held.root_link} if held is not None else None
        motion=primitives._motion_generator
        # Same batch path when candidate locked base z/roll/pitch stay unchanged.
        locked_base_indices=robot.base_idx[th.tensor([2,3,4])]
        actual_q=robot.get_joint_positions()
        same_locks=th.allclose(candidate_q[:,locked_base_indices],
                              actual_q[locked_base_indices].expand(len(candidates),-1),atol=1e-6,rtol=0)
        if same_locks:
            invalid=motion.check_collisions(candidate_q,self_collision_check=False,
                       skip_obstacle_update=skip_obstacle_update,attached_obj=attached).cpu()
        else:
            invalid_rows=[]
            for i,q in enumerate(candidates):
                # Preserve other native locked joints, including fingers. Only
                # the frame-correct base lock values change in this adapter.
                initial=actual_q.clone();initial[robot.base_idx]=q[robot.base_idx]
                invalid_rows.append(motion.check_collisions(q.unsqueeze(0),initial_joint_pos=initial,
                    self_collision_check=False,skip_obstacle_update=skip_obstacle_update if i==0 else True,
                    attached_obj=attached).cpu())
            invalid=th.cat(invalid_rows)
        for index in range(len(candidate_poses)):
            if invalid[index].item():continue
            if eef_pose is not None and not primitives._target_in_reach_of_robot(
                    eef_pose,initial_joint_pos=candidate_q[index],skip_obstacle_update=skip_obstacle_update):
                invalid[index]=True
        return ~invalid
    primitives._validate_poses=validate
    primitives._get_robot_pose_from_2d_pose=functools.wraps(original_endpoint_pose)(endpoint_pose)
    return {'applied':True,'scope':'primitive_instance_only','kind':'holonomic_frame_compatibility',
            'custom_gt_planner':False,'collision_ignores_added':False,
            'navigation_scope':'world_xy_yaw_with_current_world_height_roll_pitch',
            'root_cause_evidence':'task098_instance301_seed0_native_vs_anchor_frame_CuRobo_contrast',
            'native_source_file':str(source_path),'native_source_sha256':hashlib.sha256(source_path.read_bytes()).hexdigest(),
            'adapter_file':str(adapter_path),'adapter_sha256':hashlib.sha256(adapter_path.read_bytes()).hexdigest(),
            'observed_root_world_pose':[part.detach().cpu().tolist() for part in robot.root_link.get_position_orientation()],
            'candidate_sampling':'unchanged','world_collision_checks':'unchanged_native',
            'arm_reachability':'unchanged_native','navigation_endpoint':'native_symbolic_setter_and_settling',
            'frame_changes':['candidate world pose to anchor-relative six base joints',
                             'endpoint retains actual WORLD height/roll/pitch']}
