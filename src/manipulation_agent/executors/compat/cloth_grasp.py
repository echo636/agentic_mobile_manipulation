"""Use a native particle attachment for cloth assisted grasps."""
from functools import wraps


def install_native_cloth_grasp():
    import omnigibson as og
    from omnigibson.robots.robot import Robot
    from omnigibson.utils.constants import PrimType
    from omnigibson.utils.numpy_utils import vtarray_to_torch
    from omnigibson.utils import transform_utils as T
    import torch

    establish = Robot._establish_grasp
    create = Robot._create_assisted_grasp_joint
    release = Robot._release_grasp
    if not getattr(establish, '_mas_cloth_grasp', False):
        @wraps(establish)
        def establish_grasp(robot, target_obj, target_link_name, arm, contact_pos_world, joint_type):
            target, link_name, contact = target_obj, target_link_name, contact_pos_world
            if target.prim_type != PrimType.CLOTH:
                return establish(robot, target, link_name, arm, contact, joint_type)
            cloth = target.root_link
            positions = cloth.compute_particle_positions()
            index = torch.linalg.vector_norm(positions - contact, dim=1).argmin()
            parent_pos, parent_quat = robot.gripper_links[arm][0].get_position_orientation()
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
            from pxr import Gf, PhysxSchema, Sdf, UsdGeom, UsdPhysics, Vt

            params = constraint_params
            target = params['target_obj']
            if target.prim_type != PrimType.CLOTH:
                return create(robot, arm, params)
            # In the pinned PhysX runtime, particle attachments resolve an
            # articulation endpoint incorrectly. Use a standalone dynamic body
            # connected by a native joint; physics owns all subsequent motion.
            parent = robot.gripper_links[arm][0]
            position, orientation = parent.get_position_orientation()
            contact = position + T.quat2mat(orientation) @ (params['parent_frame_pos'] * robot.scale)
            root = f'{robot.scene.prim_path}/cloth_grasp_{robot.name}_{arm}'
            new_anchor = not og.sim.stage.GetPrimAtPath(root + '/anchor').IsValid()
            with og.sim.editing_usd():
                UsdGeom.Scope.Define(og.sim.stage, root)
                body = UsdGeom.Xform.Define(og.sim.stage, root + '/anchor')
                # Reuse the same body across release/regrasp and state restore.
                # Removing a body already used by PhysX tensor views invalidates
                # the scene's views, including the robot articulation.
                translation = body.GetPrim().GetAttribute('xformOp:translate')
                rotation = body.GetPrim().GetAttribute('xformOp:orient')
                if not translation:
                    translation = body.AddTranslateOp().GetAttr()
                    rotation = body.AddOrientOp().GetAttr()
                translation.Set(tuple(contact.tolist()))
                rotation.Set(Gf.Quatf(float(orientation[3]), Gf.Vec3f(*orientation[:3].tolist())))
                UsdPhysics.RigidBodyAPI.Apply(body.GetPrim())
                UsdPhysics.MassAPI.Apply(body.GetPrim()).CreateMassAttr(.001)
                # Both local frames are explicit. The native create_joint
                # helper renders and rebuilds ALL tensor views even when its
                # joint already exists; neither is needed for this update.
                joint = UsdPhysics.FixedJoint.Define(og.sim.stage, root + '/gripper_joint')
                PhysxSchema.PhysxJointAPI.Apply(joint.GetPrim())
                joint.CreateBody0Rel().SetTargets([Sdf.Path(parent.prim_path)])
                joint.CreateBody1Rel().SetTargets([Sdf.Path(root + '/anchor')])
                joint.CreateLocalPos0Attr(Gf.Vec3f(*params['parent_frame_pos'].tolist()))
                quat = params['parent_frame_orn']
                joint.CreateLocalRot0Attr(Gf.Quatf(float(quat[3]), Gf.Vec3f(*quat[:3].tolist())))
                joint.CreateLocalPos1Attr(Gf.Vec3f(0.))
                joint.CreateLocalRot1Attr(Gf.Quatf(1.))
                joint.CreateJointEnabledAttr(True)
                joint.CreateExcludeFromArticulationAttr(True)
                attachment = PhysxSchema.PhysxPhysicsAttachment.Define(
                    og.sim.stage, root + '/attachment')
                attachment.GetActor0Rel().SetTargets([Sdf.Path(target.root_link.prim_path)])
                attachment.GetActor1Rel().SetTargets([Sdf.Path(root + '/anchor')])
                attachment.CreatePoints0Attr(Vt.Vec3fArray([params['child_frame_pos'].tolist()]))
                attachment.CreatePoints1Attr(Vt.Vec3fArray([[0., 0., 0.]]))
                attachment.CreateAttachmentEnabledAttr(True)
            if new_anchor:
                og.sim.update_handles()
            # Reuse both native actors and the attachment. Enabling a
            # constraint needs no scene-wide tensor-view reconstruction.
            robot._ag_obj_constraints[arm] = attachment.GetPrim()
            robot._ag_obj_in_hand[arm] = target
            robot._ag_obj_constraint_params[arm] = params

        @wraps(release)
        def release_grasp(robot, arm='default'):
            from pxr import PhysxSchema
            arm = robot.default_arm if arm == 'default' else arm
            target = robot._ag_obj_in_hand[arm]
            if target is None or target.prim_type != PrimType.CLOTH:
                return release(robot, arm)
            # Preserve the native release-window bookkeeping. Disable the
            # particle constraint instead of deleting/recreating it during
            # every placement trial and grasp-state restore.
            with og.sim.editing_usd():
                PhysxSchema.PhysxPhysicsAttachment(robot._ag_obj_constraints[arm]).CreateAttachmentEnabledAttr(False)
            robot._ag_obj_constraints[arm] = None
            robot._ag_obj_constraint_params[arm] = None
            robot._ag_release_counter[arm] = 0

        establish_grasp._mas_cloth_grasp = True
        Robot._establish_grasp = establish_grasp
        Robot._create_assisted_grasp_joint = create_grasp
        Robot._release_grasp = release_grasp
    return dict(status='configured', cloth_grasp='native_particle_attachment_and_gripper_joint',
                anchor_lifecycle='reused_across_grasps; release_disables_attachment',
                rigid_grasp='unchanged', state_format='native_grasp_fields', per_frame_pose_override=False)
