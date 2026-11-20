"""Geometry-driven native pick adapter. All simulator/planner imports stay here.

Custom sampled contact frames + the OG-supported cuRobo fork; no task recipe,
authored ready pose, model waypoint list, teleport, or forced object attachment.
"""
import json
import math
from pathlib import Path
import time
import traceback
import numpy as np

from ..contracts import SkillError
from ..grasp_geometry import (bottle_grasps, resample_positions, body_path_envelope,
                              attachment_pose_in_base, recorded_pick_reference,
                              time_joint_curve, separating_contact_path)
from ..records import now
# This measured-state helper belongs to the frozen host adapter. Pick bundles
# have an isolated package namespace containing only the two versioned files.
from manipulation_agent.executors.oracle_posture import body_posture_metrics
# Embedded by publish_pick_engine.py so the live two-file loader stays compatible.
from manipulation_agent.executors.oracle_open_loop import OpenLoopPickTrial


class GeometryPick:
    planner_cache_attribute = '_geometry_pick_planners'
    planner_link_names = ('torso_link4',)
    planner_config_prefix = 'curobo'
    planner_limit_scales = (.25, .3, .3)

    def __init__(self, backend):
        self.b = backend
        self.log = backend.output / 'geometry_pick.jsonl'
        self.phase = 'preflight'
        self.arm = None
        self.planner = None

    def event(self, kind, **values):
        with self.log.open('a') as stream:
            stream.write(json.dumps({'at': now(), 'sim_step': self.b.steps,
                'request_id': getattr(self.b, '_active_request_id', None),
                'kind': kind, 'phase': self.phase, **values}, allow_nan=False)+'\n')

    def set_phase(self, phase):
        self.phase = phase
        trial = getattr(self, 'trial', None)
        if trial is not None:
            trial.enter_phase(phase)
        if self.b._primitive_context:
            self.b._phase(phase)
        self.event('phase')

    @staticmethod
    def mesh(obj):
        import omnigibson.utils.transform_utils as T
        points, faces, offset = [], [], 0
        for link in obj.links.values():
            if getattr(link, 'is_meta_link', False):
                continue
            for mesh in link.collision_meshes.values():
                p, q = mesh.get_position_orientation()
                world = (mesh.points*mesh.get_world_scale()) @ T.quat2mat(q).T + p
                points.append(world.detach().cpu().numpy())
                faces.append(mesh.faces.detach().cpu().numpy().reshape(-1, 3)+offset)
                offset += len(world)
        if not points:
            raise SkillError('missing_collision_geometry', 'Target has no collision boundary points')
        return np.concatenate(points), np.concatenate(faces)

    @classmethod
    def vertices(cls, obj):
        return cls.mesh(obj)[0]

    def gripper_geometry(self, arm):
        import omnigibson.utils.transform_utils as T
        robot, th = self.b.robot, self.b.torch
        eef = T.pose_inv(T.pose2mat(robot.eef_links[arm].get_position_orientation()))
        groups = []
        for entries in (robot.assisted_grasp_start_points[arm], robot.assisted_grasp_end_points[arm]):
            points = []
            for entry in entries:
                p, q = robot.links[entry.link_name].get_position_orientation()
                point = T.quat2mat(q) @ entry.position + p
                points.append((eef[:3, :3] @ point + eef[:3, 3]).detach().cpu().numpy())
            groups.append(np.array(points).mean(0))
        # The rays are offset into the finger by 2mm; subtract both margins.
        aperture = float(abs(groups[0][1]-groups[1][1])-.004)
        offset = np.mean(groups, axis=0)
        self.event('gripper_geometry', arm=arm, contact_offset_eef_m=offset.tolist(), aperture_m=aperture)
        return offset, aperture

    def get_planner(self, arm):
        import omnigibson as og
        import yaml
        import torch
        from curobo.types.base import TensorDeviceType
        from curobo.wrap.reacher.evaluator import TrajEvaluatorConfig
        from omnigibson.action_primitives.curobo import CuRoboMotionGenerator, CuRoboEmbodimentSelection
        cache = getattr(self.b, self.planner_cache_attribute, {})
        if arm not in cache:
            paths = self.b.robot.curobo_path
            # Navigation's closest safe endpoint need not be arm-only reachable.
            # Let the official ARM model plan torso + selected arm together.
            path = Path(paths[CuRoboEmbodimentSelection.ARM])
            if not path.is_file():
                raise SkillError('missing_robot_planner_config', f'Missing planner config {path}')
            config = yaml.safe_load(path.read_text())
            kin = config['robot_cfg']['kinematics']
            if arm == 'both':
                from ..posture_goals import POSTURE_JOINT_NAMES
                for name in POSTURE_JOINT_NAMES:
                    kin['lock_joints'].pop(name, None)
            else:
                other = 'right' if arm == 'left' else 'left'
                kin['lock_joints'].update({f'{other}_arm_joint{i}': None for i in range(1, 8)})
            kin['ee_link'] = self.b.robot.eef_link_names['right' if arm == 'both' else arm]
            kin['link_names'] = list(self.planner_link_names)
            # OG constructs RobotConfig before MotionGenConfig. Limit scaling
            # must be applied here; MotionGen rejects changing an existing config.
            kin['cspace'].update(zip(('velocity_scale', 'acceleration_scale', 'jerk_scale'),
                                    self.planner_limit_scales))
            # Actual reset model and joint limits provide posture seeds; no hand-authored ready pose.
            out = self.b.output / (self.planner_config_prefix+'_'+arm+'.yaml')
            out.write_text(yaml.safe_dump(config))
            device = 'cuda:'+str(torch.cuda.current_device())
            active_dof = sum(name not in kin['lock_joints'] for name in kin['cspace']['joint_names'])
            # This pinned cuRobo version creates default time bounds on cuda:0.
            # Pass its supported evaluator config explicitly on the planner device.
            # A longer knot interval permits the deliberately reduced joint limits.
            evaluator = TrajEvaluatorConfig.from_basic(dof=active_dof,
                min_dt=float(og.sim.get_sim_step_dt()), max_dt=.6,
                tensor_args=TensorDeviceType(device=torch.device(device)))
            # 32 knots with dt <= .6 cannot require the upstream 5000-sample
            # allocation. Size the buffer from those same limits, with margin.
            interpolation_steps = math.ceil(32*.6/float(og.sim.get_sim_step_dt()))+32
            self.event('planner_loading', arm=arm, config=str(out), device=device,
                active_dof=active_dof, time_bounds_device=str(evaluator.min_dt.device),
                interpolation_buffer_steps=interpolation_steps)
            class BaseFrameMotionGenerator(CuRoboMotionGenerator):
                def plan_batch(generator, start_state, goal_pose, plan_config,
                               link_poses=None, emb_sel=CuRoboEmbodimentSelection.DEFAULT):
                    if generator._pick_attachment_cache:
                        GeometryPick.save_planning_snapshot(generator, start_state,
                            goal_pose, link_poses, emb_sel)
                        # Use the supported graph fallback after local trajectory
                        # optimization fails; keep all collision constraints.
                        plan_config.enable_graph_attempt = 2
                        plan_config.num_graph_seeds = 1
                    generator.mg[emb_sel].store_debug_in_result = True
                    result = super().plan_batch(start_state, goal_pose, plan_config,
                        link_poses=link_poses, emb_sel=emb_sel)
                    GeometryPick.save_planning_result(generator, result[0])
                    return result

                def _attach_objects_to_robot(generator, attached_obj, attached_obj_scale,
                                             cu_js_batch, emb_sel):
                    info = GeometryPick.attach_objects_in_base(generator, attached_obj,
                        attached_obj_scale, cu_js_batch, emb_sel)
                    if info:
                        GeometryPick.attachment_diagnostics(generator, cu_js_batch, emb_sel, info)
                    return info

            cache[arm] = BaseFrameMotionGenerator(self.b.robot,
                robot_cfg_path={CuRoboEmbodimentSelection.DEFAULT: str(out)},
                device=device, batch_size=1, use_cuda_graph=False,
                motion_cfg_kwargs={'num_ik_seeds': 64, 'num_batch_ik_seeds': 64,
                    'interpolation_steps': interpolation_steps,
                    'traj_evaluator_config': evaluator, 'maximum_trajectory_dt': .6})
            setattr(self.b, self.planner_cache_attribute, cache)
            for mg in cache[arm].mg.values():
                self.event('collision_rollout_device', **self.configure_collision_rollout(mg))
                if getattr(self, 'execution_timing', None) == 'model_authored_v1':
                    from .planner_joint_limits import disable_planner_joint_limits
                    self.event('joint_limit_policy', **disable_planner_joint_limits(mg,
                        getattr(self.b, '_native_joint_limit_policy', None)))
            self.event('planner_ready', arm=arm, device=device)
        self.planner = cache[arm]
        self.planner._pick_event = self.event
        # A single retained pick must use identical geometry for planning and
        # resampled validation. The official fitter samples surface points.
        self.planner._pick_attachment_cache = {}
        self.planner._pick_snapshot_dir = self.b.output
        self.planner._pick_snapshot_key = str(getattr(self.b, '_active_request_id', 'unknown'))
        self.planner._pick_snapshot_saved = False
        self.planner._pick_result_index = 0
        self.emb = CuRoboEmbodimentSelection.DEFAULT
        self.mg = self.planner.mg[self.emb]
        return self.planner

    @staticmethod
    def attach_objects_in_base(generator, attached_obj, attached_obj_scale, cu_js_batch, emb_sel):
        """Use the same measured base frame for world meshes and carried geometry.

        The pinned OG adapter passes a world EEF pose to an attachment API whose
        meshes are already in robot-base coordinates. Keep official attachment,
        obstacle disabling and full object scale; correct only this transform.
        """
        if attached_obj is None:
            return []
        from curobo.types.math import Pose
        from omnigibson.prims.rigid_dynamic_prim import RigidDynamicPrim
        attached_info = []
        for ee_link_name, obj in attached_obj.items():
            if not isinstance(obj, RigidDynamicPrim):
                raise TypeError('Attached object must be a measured rigid link')
            paths = [mesh.prim_path for mesh in obj.collision_meshes.values()]
            if not 0 < len(paths) <= 32:
                raise ValueError('Unsupported attached collision mesh count')
            eef = generator.robot.links[ee_link_name].get_position_orientation()
            base = generator.robot.root_link.get_position_orientation()
            p, quat = attachment_pose_in_base(*(value.detach().cpu().numpy()
                                               for value in (*eef, *base)))
            pose = Pose(position=generator.tensor_args.to_device(p),
                        quaternion=generator.tensor_args.to_device(quat[[3, 0, 1, 2]]))
            link_name = generator.robot.curobo_attached_object_link_names[ee_link_name]
            scale = 1.0 if attached_obj_scale is None else attached_obj_scale[ee_link_name]
            mg = generator.mg[emb_sel]
            cache = getattr(generator, '_pick_attachment_cache', None)
            key = (link_name, tuple(paths), scale)
            if cache is not None and key in cache:
                for path in paths:
                    mg.world_coll_checker.enable_obstacle(enable=False, name=path)
                mg.attach_spheres_to_robot(sphere_tensor=cache[key], link_name=link_name)
            else:
                mg.attach_objects_to_robot(joint_state=cu_js_batch,
                    object_names=paths, ee_pose=pose, link_name=link_name,
                    scale=scale, pitch_scale=1.0, merge_meshes=True)
                if cache is not None:
                    cache[key] = mg.kinematics.kinematics_config.get_link_spheres(link_name).clone()
            attached_info.append({'obj_paths': paths, 'link_name': link_name})
        return attached_info

    @staticmethod
    def save_planning_snapshot(generator, start, goal, links, emb_sel):
        """Freeze numeric live kinematics/world for offline planning, no USD process."""
        if generator._pick_snapshot_saved:
            return
        from dataclasses import replace
        import torch
        mg = generator.mg[emb_sel]
        kin = replace(mg.robot_cfg.kinematics, kinematics_parser=None, generator_config=None)
        robot = replace(mg.robot_cfg, kinematics=kin)
        artifact = 'pick_planning_snapshot_'+generator._pick_snapshot_key+'.pt'
        torch.save({'robot_config': robot, 'world': mg.world_model.clone(),
            'start': start, 'goal': goal, 'link_poses': links,
            'attached_cache': generator._pick_attachment_cache,
            'robot_joint_names': generator.robot_joint_names},
            generator._pick_snapshot_dir / artifact)
        generator._pick_snapshot_saved = True
        generator._pick_event('planning_snapshot', artifact=artifact,
            scope='Numeric live kinematics and world; no simulator reset or physical step')

    @staticmethod
    def save_planning_result(generator, result):
        if not generator._pick_attachment_cache:
            return
        import torch
        artifact = ('pick_planning_result_'+generator._pick_snapshot_key+
                    f'_{generator._pick_result_index:02}.pt')
        torch.save(result, generator._pick_snapshot_dir / artifact)
        generator._pick_result_index += 1
        generator._pick_event('planning_debug_result', artifact=artifact)

    @staticmethod
    def attachment_diagnostics(generator, initial, emb_sel, info):
        """Record actual carried bounds and start collision costs without stepping."""
        import torch
        from curobo.types.state import JointState
        mg = generator.mg[emb_sel]
        js = JointState.from_position(initial.position[:1], joint_names=initial.joint_names)
        spheres = mg.compute_kinematics(js).robot_spheres.unsqueeze(1)
        kc = mg.kinematics.kinematics_config
        mask = torch.zeros(spheres.shape[-2], dtype=torch.bool, device=spheres.device)
        links = []
        for entry in info:
            indices = kc.get_sphere_index_from_link_name(entry['link_name'])
            mask[indices] = True
            local = kc.get_link_spheres(entry['link_name'])
            valid = local[local[:, 3] > 0]
            links.append({'link': entry['link_name'], 'active_spheres': len(valid),
                'max_local_center_distance_m': float(valid[:, :3].norm(dim=-1).max()) if len(valid) else None})
        without, only = spheres.clone(), spheres.clone()
        without[..., mask, 3] = -10.
        only[..., ~mask, 3] = -10.
        world = mg.rollout_fn.primitive_collision_constraint
        self_cost = mg.rollout_fn.robot_self_collision_constraint
        generator._pick_event('attached_start_collision', carried_links=links,
            world_cost=float(world.forward(spheres).max()),
            carried_world_cost=float(world.forward(only).max()),
            self_cost=float(self_cost.forward(spheres).max()),
            self_without_carried_cost=float(self_cost.forward(without).max()))

    @staticmethod
    def configure_collision_rollout(mg):
        """Keep the pinned graph safety rollout on the actual planner GPU.

        The upstream GraphConfig factory omits tensor_args for ArmBaseConfig.
        Rebuild this official rollout with the shared robot/world configuration;
        no upstream edits or collision exclusions are required.
        """
        previous = str(mg.rollout_fn.tensor_args.device)
        previous_graph_bounds = str(mg.graph_planner.bounds.device)
        if (mg.rollout_fn.tensor_args.device != mg.tensor_args.device or
                mg.graph_planner.bounds.device != mg.tensor_args.device):
            from curobo.rollout.arm_base import ArmBase, ArmBaseConfig
            from curobo.util_file import get_task_configs_path, join_path, load_yaml
            base = load_yaml(join_path(get_task_configs_path(), 'base_cfg.yml'))
            graph = load_yaml(join_path(get_task_configs_path(), 'graph.yml'))
            cfg = ArmBaseConfig.from_dict(mg.robot_cfg, graph['model'], base['cost'],
                base['constraint'], base['convergence'], base['world_collision_checker_cfg'],
                world_coll_checker=mg.world_coll_checker, tensor_args=mg.tensor_args)
            rollout = ArmBase(cfg)
            mg.rollout_fn = rollout
            # The same omitted tensor_args also contaminated graph bounds,
            # Halton sampling and velocity buffers. Rebuild the official graph
            # planner from its original settings and the corrected rollout.
            from dataclasses import fields
            from curobo.graph.graph_base import GraphConfig
            previous_graph = mg.graph_planner
            settings = {field.name: getattr(previous_graph, field.name) for field in fields(GraphConfig)}
            settings.update(rollout_fn=rollout, safety_rollout_fn=rollout,
                bounds=rollout.action_bounds, tensor_args=mg.tensor_args,
                distance_weight=rollout.cspace_config.cspace_distance_weight,
                bias_node=rollout.cspace_config.retract_config.view(1, -1))
            mg.graph_planner = type(previous_graph)(GraphConfig(**settings))
            mg._rollout_list = None
        return {'previous_device': previous, 'device': str(mg.rollout_fn.tensor_args.device),
                'previous_graph_bounds_device': previous_graph_bounds,
                'graph_bounds_device': str(mg.graph_planner.bounds.device)}

    def body_costs(self, restore):
        if 'torso_link4' not in self.planner_link_names:
            return
        from curobo.rollout.arm_reacher import ArmReacher
        # Keep the upper body vertical while allowing yaw and a geometric squat.
        # The final attached trajectory restores the measured initial body pose.
        reach = self.planner.tensor_args.to_device([1, 1, 1, 1, 1, 1] if restore else [1, 1, 0, 0, 0, 0])
        hold = self.planner.tensor_args.to_device([1, 1, 0, 0, 0, 0])
        for rollout in self.mg.get_all_rollout_instances():
            if isinstance(rollout, ArmReacher):
                cost = rollout._link_pose_costs['torso_link4']
                cost.reach_partial_pose(reach)
                cost.hold_partial_pose(hold)
                rollout._link_pose_convergence['torso_link4'].reach_partial_pose(reach)

    def body_clearance(self, q, *, restore=False):
        if 'torso_link4' not in self.planner_link_names:
            self.event('body_posture_policy', torso_pose_target=False,
                       extra_tilt_or_height_envelope=False,
                       joint_position_limits=(self.joint_position_policy()
                           if hasattr(self, 'joint_position_policy') else 'native_robot_model'))
            return True
        from curobo.types.state import JointState
        import omnigibson.utils.transform_utils as T
        js = JointState(position=self.planner.tensor_args.to_device(q),
            joint_names=self.planner.robot_joint_names).get_ordered_joint_state(self.mg.kinematics.joint_names)
        body = self.mg.compute_kinematics(js).link_poses['torso_link4']
        p, quat = self.b.robot.root_link.get_position_orientation()
        root_r = T.quat2mat(quat).cpu().numpy()
        positions = body.position.detach().cpu().numpy().reshape(-1, 3) @ root_r.T + p.cpu().numpy()
        quats = body.quaternion.detach().cpu()[..., [1, 2, 3, 0]]
        rotations = root_r @ T.quat2mat(quats).numpy().reshape(-1, 3, 3)
        result = body_path_envelope(positions, rotations, self.home_body[0][2], restore)
        self.event('planned_body_envelope', **result)
        return result['valid']

    def plan(self, position, rotation, *, initial=None, ignore_target=False, approach=False, attached=False,
             restore_body=False):
        import omnigibson as og
        import omnigibson.utils.transform_utils as T
        th = self.b.torch
        self.planner.update_obstacles(ignore_objects=[self.obj] if ignore_target else None)
        self.body_costs(restore_body)
        target_pos = {self.b.robot.eef_link_names[self.arm]: th.as_tensor(position, dtype=th.float32)}
        target_quat = {self.b.robot.eef_link_names[self.arm]: T.mat2quat(th.as_tensor(rotation, dtype=th.float32))}
        if 'torso_link4' in self.planner_link_names:
            target_pos['torso_link4'] = th.as_tensor(self.home_body[0], dtype=th.float32)
            target_quat['torso_link4'] = th.as_tensor(self.home_body[1], dtype=th.float32)
        t0 = time.monotonic()
        results = self.planner.compute_trajectories(
            target_pos=target_pos, target_quat=target_quat,
            initial_joint_pos=initial, is_local=False, max_attempts=6, timeout=12.,
            skip_obstacle_update=True, return_full_result=True,
            motion_constraint=[1, 1, 1, 1, 1, 0] if approach else None,
            attached_obj={self.b.robot.eef_link_names[self.arm]: self.link} if attached else None,
            attached_obj_scale={self.b.robot.eef_link_names[self.arm]: 1.0} if attached else None)
        result = results[0]
        success = bool(result.success.flatten()[0])
        def error_min(value):
            if value is None:
                return None
            scalar = float(value.min())
            return scalar if math.isfinite(scalar) else str(scalar)
        self.event('plan_result', success=success, status=str(result.status),
                   duration_seconds=time.monotonic()-t0, ignore_target=ignore_target,
                   attached_object_scale=1.0 if attached else None,
                   valid_query=bool(result.valid_query), attempts=int(result.attempts),
                   trajopt_attempts=int(result.trajopt_attempts),
                   position_error_m=error_min(result.position_error),
                   rotation_error_rad=error_min(result.rotation_error))
        if not success:
            return None
        path = result.get_paths()[0]
        full = self.mg.get_full_js(path).get_ordered_joint_state(self.planner.robot_joint_names)
        # get_paths has interpolation_dt spacing, not optimized_dt (the knot spacing).
        source_dt = float(self.mg.interpolation_dt)
        control_dt = float(og.sim.get_sim_step_dt())
        q = resample_positions(full.position.detach().cpu().numpy(), source_dt, control_dt)
        q = th.as_tensor(q, dtype=th.float32)
        if not self.body_clearance(q, restore=restore_body):
            return None
        if bool(self.planner.check_collisions(q, initial_joint_pos=initial,
                skip_obstacle_update=True,
                attached_obj={self.b.robot.eef_link_names[self.arm]: self.link} if attached else None,
                attached_obj_scale={self.b.robot.eef_link_names[self.arm]: 1.0} if attached else None).any()):
            self.event('resampled_collision_rejected')
            return None
        if ignore_target and not attached and not self.target_clearance(q):
            self.event('target_pair_collision_rejected')
            return None
        index = len(list(self.b.output.glob('pick_trajectory_*.npz')))
        np.savez(self.b.output/f'pick_trajectory_{index:03}.npz',
            position=q.numpy(), control_dt=control_dt, source_dt=source_dt,
            joint_names=np.array(self.planner.robot_joint_names),
            planner_position=full.position.detach().cpu().numpy(),
            planner_velocity=full.velocity.detach().cpu().numpy() if full.velocity is not None else [],
            planner_acceleration=full.acceleration.detach().cpu().numpy() if full.acceleration is not None else [])
        self.event('trajectory_validated', samples=len(q), control_dt=control_dt,
            artifact=f'pick_trajectory_{index:03}.npz', target_finger_contact_only=ignore_target and not attached)
        return q

    def target_clearance(self, q, *, vertices=None):
        """Pair-specific exception: selected fingers may touch target; all other links may not.

        cuRobo checks the rest of the world and self collision. A conservative
        convex-hull plane test checks every other robot sphere against target.
        """
        from scipy.spatial import ConvexHull
        import omnigibson.utils.transform_utils as T
        from curobo.types.state import JointState
        p, quat = self.b.robot.root_link.get_position_orientation()
        vertices = self.vertices(self.obj) if vertices is None else vertices
        vertices = (vertices-p.cpu().numpy()) @ T.quat2mat(quat).cpu().numpy()
        hull = ConvexHull(vertices).equations
        js = JointState(position=self.planner.tensor_args.to_device(q),
                        joint_names=self.planner.robot_joint_names).get_ordered_joint_state(self.mg.kinematics.joint_names)
        spheres = self.mg.compute_kinematics(js).robot_spheres.detach().cpu().numpy()
        kc = self.mg.kinematics.kinematics_config
        allowed = [kc.link_name_to_idx_map[link.body_name] for link in self.b.robot.finger_links[self.arm]]
        mask = ~np.isin(kc.link_sphere_idx_map.cpu().numpy(), allowed)
        spheres = spheres[:, mask]
        for batch in np.array_split(spheres, max(1, math.ceil(len(spheres)/64))):
            plane_distance = batch[..., :3] @ hull[:, :3].T + hull[:, 3]
            separated = plane_distance.max(-1) > batch[..., 3]+.001
            if np.any((batch[..., 3] > 0) & ~separated):
                return False
        return True

    def stream(self, q, *, retained=False):
        trial = getattr(self, 'trial', None)
        if trial is not None:
            return trial.stream(q, self.arm)
        indices = self.b.torch.cat((self.b.robot.trunk_control_idx, self.b.robot.arm_control_idx[self.arm]))
        start = self.b.robot.get_joint_positions()
        if float((q[0, indices].to(start)-start[indices]).abs().max()) > .12:
            raise SkillError('trajectory_start_changed', 'Measured state differs from planned start', changed=True)
        worst = 0.
        for i, command in enumerate(q[1:]):
            measured = self.b.robot.get_joint_positions()
            error = float((command[indices].to(measured)-measured[indices]).abs().max())
            worst = max(worst, error)
            if error > .35:
                self.b._command_q = measured.clone()
                raise SkillError('trajectory_tracking_error', f'Joint tracking error {error:.3f} rad', changed=True)
            self.b._command_q[indices] = command[indices].to(self.b._command_q)
            self.b._trace_waypoint = i
            self.b._step(self.b._hold_action())
            if 'torso_link4' in self.planner_link_names:
                p, quat = self.b.robot.links['torso_link4'].get_position_orientation()
                base = self.b.robot.root_link.get_position_orientation()[0]
                body = body_posture_metrics(base.tolist(), p.tolist(), quat.tolist())
                if (body['upper_body_tilt_deg'] > 12 or float(p[2]) < self.home_body[0][2]-.45):
                    self.b._command_q = self.b.robot.get_joint_positions().clone()
                    raise SkillError('body_posture_envelope_exceeded', 'Measured torso left the planned execution envelope', changed=True)
            if retained and self.b._held(self.arm) != self.obj.name:
                raise SkillError('retention_lost', 'Native retention lost during lift', changed=True)
        self.event('trajectory_executed', samples=len(q)-1, max_tracking_error_rad=worst)

    def certify_initial_support(self, spheres, depths, up):
        """Identify existing carried contact against measured upward support meshes."""
        import trimesh
        target_paths = {mesh.prim_path for mesh in self.link.collision_meshes.values()}
        contacts = spheres[depths > 0]
        if not len(contacts):
            return []
        def world_mesh(obstacle):
            mesh = obstacle.get_trimesh_mesh(process=False, process_color=False).copy()
            mesh.apply_transform(obstacle.get_transform_matrix())
            return mesh
        vertices = np.concatenate([world_mesh(mesh).vertices for mesh in self.mg.world_model.mesh
                                   if mesh.name in target_paths])
        low = (contacts[:, :3]-contacts[:, 3:]).min(0)
        high = (contacts[:, :3]+contacts[:, 3:]).max(0)
        supports = []
        for obstacle in self.mg.world_model.mesh:
            if obstacle.name in target_paths:
                continue
            mesh = world_mesh(obstacle)
            if np.any(mesh.bounds[1] < low) or np.any(mesh.bounds[0] > high):
                continue
            points, distances, triangles = trimesh.proximity.closest_point(mesh, contacts[:, :3])
            inside = mesh.contains(contacts[:, :3]) if mesh.is_watertight else np.zeros(len(contacts), bool)
            gaps = np.where(inside, -distances, distances)-contacts[:, 3]
            active = gaps < 0
            if not active.any():
                continue
            normals = mesh.face_normals[triangles[active]]
            if not mesh.is_watertight or np.any(normals @ up < .99):
                return None
            _, target_distance, _ = trimesh.proximity.closest_point(mesh, vertices)
            target_gap = np.where(mesh.contains(vertices), -target_distance, target_distance)
            if target_gap.min() < -.0001:
                return None
            supports.append(dict(obstacle=obstacle.name, min_sphere_gap_m=float(gaps.min()),
                                 min_target_vertex_gap_m=float(target_gap.min()),
                                 normal_up_alignment=float((normals @ up).min())))
        return supports or None

    def time_support_departure(self, waypoints, rates, dt):
        """Historical contact-departure timing and its validation samples."""
        q, timing = time_joint_curve(waypoints, *rates, dt)
        return q, timing, q

    def plan_support_departure(self, goal, rotation):
        """Generate and certify a separating Cartesian lift before body recovery.

        The official optimizer rejects the immutable initial support contact.
        Keep full-scale geometry and hard world/self checks. Only inherited
        shallow support contact may decrease during the first millimetres.
        """
        import omnigibson as og
        import omnigibson.utils.transform_utils as T
        from curobo.types.state import JointState
        from curobo.types.math import Pose
        from curobo.geom.sdf.world import CollisionQueryBuffer
        from scipy.spatial.transform import Rotation
        self.set_phase('plan_support_departure')
        self.planner.update_obstacles()
        self.body_costs(False)
        full_start = JointState.from_position(self.planner.tensor_args.to_device(
            self.b.robot.get_joint_positions().unsqueeze(0)), joint_names=self.planner.robot_joint_names)
        self.planner.update_locked_joints(full_start, self.emb)
        start = full_start.get_ordered_joint_state(self.mg.kinematics.joint_names)
        info = self.planner._attach_objects_to_robot(
            {self.b.robot.eef_link_names[self.arm]: self.link},
            {self.b.robot.eef_link_names[self.arm]: 1.0}, start, self.emb)
        margins = []
        try:
            args, th = self.planner.tensor_args, self.b.torch
            state = self.mg.compute_kinematics(start)
            root_p, root_q = self.b.robot.root_link.get_position_orientation()
            root_rotation = T.quat2mat(root_q).cpu().numpy()
            up = root_rotation.T @ np.array([0., 0., 1.])
            mask = np.zeros(state.robot_spheres.shape[-2], dtype=bool)
            for entry in info:
                indices = self.mg.kinematics.kinematics_config.get_sphere_index_from_link_name(entry['link_name'])
                mask[indices.cpu().numpy()] = True
            def penetration(spheres):
                buffer = CollisionQueryBuffer()
                buffer.update_buffer_shape(spheres.shape, args, self.mg.world_coll_checker.collision_types)
                return self.mg.world_coll_checker.get_sphere_distance(spheres.contiguous(), buffer,
                    args.to_device([1.]), args.to_device([0.]), return_loss=True).squeeze(1)
            initial_depths = penetration(state.robot_spheres.unsqueeze(1))[0].detach().cpu().numpy()
            if np.any(initial_depths[~mask] > 0) or initial_depths.max() > .002:
                return None
            supports = self.certify_initial_support(state.robot_spheres[0].detach().cpu().numpy(),
                initial_depths, up)
            self.event('initial_support_certificate', supports=supports,
                       initial_depth_m=float(initial_depths.max()), full_object_scale=1.0)
            if supports is None:
                return None
            # Remove only the soft 35mm approach margin for local contact
            # departure IK. Hard collision constraints remain enabled, and the
            # final timed path is checked independently with original geometry.
            seen = set()
            for rollout in self.mg.ik_solver.get_all_rollout_instances():
                margin = rollout.primitive_collision_cost.activation_distance
                if id(margin) not in seen:
                    margins.append((margin, margin.clone())); seen.add(id(margin)); margin.fill_(0.)
            link_poses = None
            if 'torso_link4' in self.planner_link_names:
                body_p, body_q = attachment_pose_in_base(*self.home_body,
                    root_p.cpu().numpy(), root_q.cpu().numpy())
                link_poses = {'torso_link4': Pose(position=args.to_device(body_p).unsqueeze(0),
                    quaternion=args.to_device(body_q[[3, 0, 1, 2]]).unsqueeze(0))}
            actual_eef = self.b.robot.eef_links[self.arm].get_position_orientation()[0].cpu().numpy()
            height = float(np.dot(goal-actual_eef, np.array([0., 0., 1.])))
            if height <= 0 or height > .25:
                return None
            current = start.position.clone(); waypoints = [current[0].clone()]
            for rise in np.linspace(height/math.ceil(height/.005), height, math.ceil(height/.005)):
                target = state.ee_pose.clone()
                target.position += args.to_device(up*rise)
                result = self.mg.ik_solver.solve_single(target, retract_config=current,
                    seed_config=current.unsqueeze(1), return_seeds=1, num_seeds=1,
                    use_nn_seed=False, link_poses=link_poses)
                if not bool(result.success.flatten()[0]):
                    self.event('support_departure_ik_failed', rise_m=float(rise))
                    return None
                current = result.solution.reshape(1, -1)
                waypoints.append(current[0].clone())
            limits = self.mg.robot_cfg.kinematics.get_joint_limits()
            rates = [getattr(limits, name).abs().min(dim=0).values.detach().cpu().numpy()
                     for name in ('velocity', 'acceleration', 'jerk')]
            dt = float(og.sim.get_sim_step_dt())
            q, timing, validation = self.time_support_departure(
                th.stack(waypoints).detach().cpu().numpy(), rates, dt)
            active = JointState.from_position(args.to_device(q), joint_names=self.mg.kinematics.joint_names)
            validation_active = active if validation is q else JointState.from_position(
                args.to_device(validation), joint_names=self.mg.kinematics.joint_names)
            curve = self.mg.compute_kinematics(validation_active)
            spheres = curve.robot_spheres.unsqueeze(1)
            rotations = Rotation.from_quat(curve.ee_pose.quaternion.detach().cpu().numpy()[:, [1, 2, 3, 0]]).as_matrix()
            certificate = separating_contact_path(penetration(spheres).detach().cpu().numpy(), mask,
                curve.ee_pose.position.detach().cpu().numpy(), rotations, up)
            self_cost = float(self.mg.rollout_fn.robot_self_collision_constraint.forward(spheres).max())
            self.event('support_departure_path_certificate', **certificate, timing=timing,
                self_collision_cost=self_cost, hard_collision_checks=True)
            full = self.mg.get_full_js(active).get_ordered_joint_state(self.planner.robot_joint_names)
            q = full.position.detach().cpu()
            if not certificate['valid'] or self_cost > 0 or not self.body_clearance(q):
                return None
            index = len(list(self.b.output.glob('pick_trajectory_*.npz')))
            artifact = f'pick_trajectory_{index:03}.npz'
            np.savez(self.b.output/artifact, position=q.numpy(), control_dt=dt, source_dt=dt,
                     joint_names=np.array(self.planner.robot_joint_names), planner_position=q.numpy())
            self.event('trajectory_validated', samples=len(q), control_dt=dt, artifact=artifact,
                       initial_separating_support_contact=certificate)
            return q
        finally:
            for margin, original in margins:
                margin.copy_(original)
            self.planner._detach_objects_from_robot(info, self.emb)

    def plan_lift_paths(self, goal, rotation):
        # First plan to an endpoint above the grasp while allowing the measured
        # squat. The approach constraint uses EEF axes and cannot impose world
        # vertical motion here. Validate body-restoring recovery before moving.
        self.set_phase('plan_lift_clearance')
        clearance = self.plan(goal, rotation, attached=True)
        if clearance is None:
            clearance = self.plan_support_departure(goal, rotation)
        if clearance is not None:
            # Local lifting can itself restore the original upright/height
            # envelope. Do not require an extra full-pose torso recovery when
            # this unchanged final posture guard already passes.
            if self.body_clearance(clearance, restore=True):
                self.event('lift_endpoint_restores_body')
                return [('clearance', clearance)]
            self.set_phase('plan_lift_recovery')
            for inward in (.4, .6, .2, 0., .8):
                hold_goal = goal.copy()
                hold_goal[:2] += inward*(self.home_body[0][:2]-hold_goal[:2])
                self.event('lift_recovery_candidate', inward_fraction=inward,
                           eef_world_m=hold_goal.tolist())
                recovery = self.plan(hold_goal, rotation, initial=clearance[-1],
                                     attached=True, restore_body=True)
                if recovery is not None:
                    return [('clearance', clearance), ('recovery', recovery)]
        self.set_phase('plan_attached_lift')
        for inward in (0., .2, .4, .6, .8):
            hold_goal = goal.copy()
            hold_goal[:2] += inward*(self.home_body[0][:2]-hold_goal[:2])
            self.event('lift_candidate', inward_fraction=inward, eef_world_m=hold_goal.tolist())
            lift = self.plan(hold_goal, rotation, attached=True, restore_body=True)
            if lift is not None:
                return [('direct', lift)]
        return []

    def lift_and_hold(self, lift_m, before, rejected):
        self.set_phase('plan_attached_lift')
        import omnigibson.utils.transform_utils as T
        p, quat = self.b.robot.eef_links[self.arm].get_position_orientation()
        # Search upward/inward hold poses; restoring the torso may need
        # to bring a bottle back from beyond the arm-only reach envelope.
        # These are backend geometric candidates, never authored joints.
        goal = p.cpu().numpy().copy(); goal[2] += max(0., float(before[2])+lift_m-float(self.obj.get_position_orientation()[0][2]))+.02
        paths = self.plan_lift_paths(goal, T.quat2mat(quat).cpu().numpy())
        if not paths:
            return {'reached': False, 'verified': False, 'phase': self.phase,
                'reason': 'attached_lift_planning_failed', 'arm': self.arm,
                'retention': self.b._retention(self.arm, self.obj, self.link)}
        self.set_phase('lift')
        trial = getattr(self, 'trial', None)
        if trial is not None:
            trial.stream_paths(paths, self.arm,
                on_segment=lambda name: self.event('lift_segment', segment=name))
        else:
            for segment, path in paths:
                self.event('lift_segment', segment=segment, samples=len(path))
                self.stream(path, retained=True)
        self.set_phase('hold')
        rel0 = self.b._relative(self.obj.get_position_orientation(),
            self.b.robot.eef_links[self.arm].get_position_orientation())[0]
        drift, retained, lifts = 0., 0, []
        for _ in range(90):
            self.b._step(self.b._hold_action())
            retained += int(self.b._held(self.arm) == self.obj.name)
            current = self.b._relative(self.obj.get_position_orientation(),
                self.b.robot.eef_links[self.arm].get_position_orientation())[0]
            drift = max(drift, float((current-rel0).norm()))
            lifts.append(float(self.obj.get_position_orientation()[0][2]-before[2]))
        verified = retained == 90 and min(lifts[-60:]) >= lift_m and drift <= .03
        if trial is not None:
            baseline_height = self.home_body[0][2] - trial.initial_base_z
            verified &= len(trial.samples) == 90 and all(
                row['body_posture']['upper_body_tilt_deg'] <= 5 and
                row['body_posture']['upper_body_height_above_base_m'] >= baseline_height-.08
                for row in trial.samples[-60:])
        result = {'reached': True, 'verified': verified, 'phase': self.phase,
            'arm': self.arm, 'lifted_m': min(lifts[-60:]), 'required_lift_m': lift_m,
            'hold_samples': 90, 'retained_samples': retained, 'relative_position_drift_m': drift,
            'retention': self.b._retention(self.arm, self.obj, self.link),
            'grasping_mode': 'native_assisted', 'planner': 'curobo',
            'motion_source': 'geometry_pick_v1', 'rejected_arms': rejected}
        self.event('result', **result)
        return result

    def resume_retained(self, lift_m):
        retention = self.b._retention(self.arm, self.obj, self.link)
        if not retention['retained']:
            raise SkillError('unverified_retention', 'Cannot continue without measured native retention')
        records = [json.loads(line) for line in self.log.read_text().splitlines()]
        origins = [row for row in records if row.get('kind') == 'retention'
                   and row.get('retained') is True
                   and row.get('native_grasp', {}).get('object_id') == self.obj.name]
        if not origins:
            raise SkillError('missing_pick_reference', 'No recorded retention source for this target')
        events = [json.loads(line) for line in (self.b.output / 'events.jsonl').read_text().splitlines()]
        reference = recorded_pick_reference(events, origins[-1]['request_id'], self.obj.name)
        self.home_body = (np.asarray(reference['body_position_world_m']),
                          np.asarray(reference['body_quaternion_xyzw']))
        before = self.b.torch.as_tensor(reference['target_position_world_m'], dtype=self.b.torch.float32)
        self.b._primitive_context = {'operation': 'pick', 'phase': 'resume_retained', 'arm': self.arm,
            'object': self.obj, 'link': self.link, 'contact_seen': True,
            'contact_links_seen': set(), 'positions': []}
        self.b._trace_arm = self.arm
        self.set_phase('resume_retained')
        self.event('resume_retained', arm=self.arm, reference=reference, retention=retention,
                   skipped_phases=['open', 'generate_candidates', 'pregrasp', 'approach', 'close'])
        self.get_planner(self.arm)
        return self.lift_and_hold(lift_m, before, [])

    def execute(self, operation, target, arm='auto', lift_m=.10):
        with OpenLoopPickTrial(self.b, self.event) as trial:
            self.trial = trial
            result = self._execute_pick(operation, target, arm, lift_m)
            trial.accepted = result.get('verified') is True
            if not trial.accepted:
                trial.manifest['postcondition_failure'] = result.get('reason', 'terminal_verification_failed')
        result['execution'] = dict(trial.manifest, trial=str(trial.root))
        # Evidence failure is a failed attempt even if endpoint kinematics passed.
        if not trial.accepted:
            result['verified'] = False
        return result

    def _execute_pick(self, operation, target, arm='auto', lift_m=.10):
        if operation != 'pick' or arm not in ('auto', 'left', 'right') or not .05 <= lift_m <= .20:
            raise SkillError('invalid_arguments', 'Invalid geometric pick intent')
        binding = self.b.env.task.object_scope.get(target)
        self.obj = binding if binding is not None else self.b._resolve_object(target)
        if self.obj.fixed_base:
            raise SkillError('fixed_target', 'Pick requires a free object')
        self.link = self.obj.root_link
        self.home_body = tuple(v.detach().cpu().numpy().copy()
            for v in self.b.robot.links['torso_link4'].get_position_orientation())
        before = self.obj.get_position_orientation()[0].clone()
        self.event('intent', operation=operation, target=target, object_id=self.obj.name,
            lift_m=lift_m, requested_arm=arm, grasping_mode='native_assisted',
            navigation='empty_ideal_kinematic;held_forbidden')
        arms = list(self.b.robot.arm_names) if arm == 'auto' else [arm]
        arms.sort(key=lambda a: float((self.b.robot.eef_links[a].get_position_orientation()[0]-before).norm()))
        rejected = []
        try:
            holders = {a: self.b._held(a) for a in self.b.robot.arm_names if self.b._held(a) is not None}
            if holders:
                if len(holders) != 1 or next(iter(holders.values())) != self.obj.name:
                    raise SkillError('hand_occupied', 'Only the retained pick target can resume')
                self.arm = next(iter(holders))
                if arm not in ('auto', self.arm):
                    raise SkillError('hand_occupied', 'Requested arm differs from the actual retained arm')
                return self.resume_retained(lift_m)
            for selected in arms:
                self.arm = selected
                self.b._primitive_context = {'operation': 'pick', 'phase': 'open', 'arm': selected,
                    'object': self.obj, 'link': self.link, 'contact_seen': False,
                    'contact_links_seen': set(), 'positions': []}
                self.b._trace_arm = selected
                self.set_phase('open')
                self.b._set_gripper(selected, 'open')
                offset, aperture = self.gripper_geometry(selected)
                self.set_phase('generate_candidates')
                vertices, faces = self.mesh(self.obj)
                hand = self.b.robot.eef_links[selected].get_position_orientation()[0].cpu().numpy()
                candidates = bottle_grasps(vertices, hand, offset, aperture, faces=faces)
                self.event('candidates', arm=selected, count=len(candidates),
                    geometry_source='collision_mesh_cross_sections',
                    frames=[{'contact_world_m': c.contact.tolist(), 'eef_world_m': c.eef.tolist(),
                             'rotation_world': c.rotation.tolist(), 'jaw_width_m': c.width} for c in candidates])
                if not candidates:
                    rejected.append({'arm': selected, 'reason': 'no_aperture_compatible_candidates'})
                    continue
                self.set_phase('plan_candidates')
                self.get_planner(selected)
                choice = None
                for n, c in enumerate(candidates):
                    self.event('candidate_attempt', index=n, arm=selected)
                    pre = self.plan(c.pregrasp, c.rotation)
                    if pre is None:
                        continue
                    approach = self.plan(c.eef, c.rotation, initial=pre[-1], ignore_target=True, approach=True)
                    if approach is not None:
                        choice = c, pre, approach
                        break
                if choice is None:
                    rejected.append({'arm': selected, 'reason': 'all_candidates_failed_planning'})
                    continue
                c, pre, approach = choice
                trial = getattr(self, 'trial', None)
                if trial is not None:
                    trial.stream_paths([('pregrasp', pre), ('approach', approach)],
                                       self.arm, on_segment=self.set_phase)
                else:
                    self.set_phase('pregrasp')
                    self.stream(pre)
                    self.set_phase('approach')
                    self.stream(approach)
                # Only contact establishment needs settling; no per-waypoint waits.
                self.set_phase('close')
                self.b._set_gripper(selected, 'close')
                retention = self.b._retention(selected, self.obj, self.link)
                self.event('retention', **retention)
                if not retention['retained']:
                    return {'reached': True, 'verified': False, 'phase': self.phase,
                        'reason': 'no_native_retention', 'arm': selected, 'retention': retention}
                return self.lift_and_hold(lift_m, before, rejected)
            return {'reached': False, 'verified': False, 'phase': self.phase,
                    'reason': 'no_reachable_collision_free_grasp', 'rejected_arms': rejected}
        except SkillError:
            raise
        except Exception as exc:
            self.event('exception', exception=type(exc).__name__, message=str(exc)[:1200],
                traceback=traceback.format_exc())
            raise SkillError('geometry_pick_failed', f'{self.phase}: {type(exc).__name__}: {str(exc)[:500]}', changed=True) from exc
        finally:
            self.b._primitive_context = None
            self.b._trace_arm = self.b._trace_target = self.b._trace_waypoint = None
