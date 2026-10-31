"""Use a native particle attachment for cloth assisted grasps."""
from functools import wraps


def install_native_cloth_grasp():
    import omnigibson as og
    from omnigibson.robots.robot import Robot
    from omnigibson.utils.constants import PrimType
    from omnigibson.utils.numpy_utils import vtarray_to_torch
    from omnigibson.utils import transform_utils as T
    import torch
    from pxr import PhysxSchema, Sdf, Vt

    establish = Robot._establish_grasp
    create = Robot._create_assisted_grasp_joint
    if not getattr(establish, '_mas_cloth_grasp', False):
        @wraps(establish)
        def establish_grasp(robot, target_obj, target_link_name, arm, contact_pos_world, joint_type):
            target, link_name, contact = target_obj, target_link_name, contact_pos_world
            if target.prim_type != PrimType.CLOTH:
                return establish(robot, target, link_name, arm, contact, joint_type)
            cloth = target.root_link
            positions = cloth.compute_particle_positions()
            index = torch.linalg.vector_norm(positions - contact, dim=1).argmin()
            parent_pos, parent_quat = robot.eef_links[arm].get_position_orientation()
            local = T.quat2mat(parent_quat).T @ (positions[index] - parent_pos) / robot.scale
            # Reuse the native grasp-state fields, including serialized state.
            # A cloth attachment identifies the vertex in the mesh's rest frame;
            # a rigid joint instead uses the current link-relative contact.
            params = dict(target_obj=target, target_link_name=link_name,
                          parent_frame_pos=local, parent_frame_orn=contact.new_tensor([0., 0., 0., 1.]),
                          child_frame_pos=vtarray_to_torch(cloth.get_attribute('points_default'))[index],
                          child_frame_orn=contact.new_tensor([0., 0., 0., 1.]), joint_type='FixedJoint')
            robot._create_assisted_grasp_joint(arm, params)

        @wraps(create)
        def create_grasp(robot, arm, constraint_params):
            params = constraint_params
            target = params['target_obj']
            if target.prim_type != PrimType.CLOTH:
                return create(robot, arm, params)
            parent = robot.eef_links[arm]
            with og.sim.editing_usd():
                attachment = PhysxSchema.PhysxPhysicsAttachment.Define(
                    og.sim.stage, parent.prim_path + '/ag_constraint')
                attachment.GetActor0Rel().SetTargets([Sdf.Path(target.root_link.prim_path)])
                attachment.GetActor1Rel().SetTargets([Sdf.Path(parent.prim_path)])
                attachment.CreatePoints0Attr(Vt.Vec3fArray([params['child_frame_pos'].tolist()]))
                attachment.CreatePoints1Attr(Vt.Vec3fArray([params['parent_frame_pos'].tolist()]))
            og.sim.update_handles()
            robot._ag_obj_constraints[arm] = attachment.GetPrim()
            robot._ag_obj_in_hand[arm] = target
            robot._ag_obj_constraint_params[arm] = params

        establish_grasp._mas_cloth_grasp = True
        Robot._establish_grasp = establish_grasp
        Robot._create_assisted_grasp_joint = create_grasp
    return dict(status='configured', cloth_grasp='native_PhysxPhysicsAttachment_at_selected_contact',
                rigid_grasp='unchanged', state_format='native_grasp_fields', per_frame_pose_override=False)
