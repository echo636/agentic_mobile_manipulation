"""Oracle geometry + native motor execution for pinned OmniGibson 3.9.2.

The RGB adapter supplies cameras and the original navigate_to planner/follower.
Manipulation uses only native IK/joint/gripper execution. The legacy navigation
phase retains its explicit ideal base/posture/held-object projection; those hooks
are disabled outside navigation. Old semantic manipulation remains inaccessible.
"""
import hashlib
import itertools
import json
import math
import os
from pathlib import Path

from ..contracts import SkillError
from ..append_log import LineWriter
from ..deadline import DEADLINE_STEP_SENTINEL
from ..omnigibson_backend import OmniGibsonBackend
from ..oracle_contracts import checked_pose, jacobian_row_index, rotation_error_vector
from ..records import now
from .omnigibson_rgb import RGBBackend
from .oracle_geometry import OracleGeometry
from .oracle_joint_trace import task_joint_handles, read_joint_positions
from .oracle_cameras import OracleWristCameras
from .oracle_primitives import OracleManipulationPrimitives
from .profiling import action_timing, component


class OracleWaypointBackend(OracleWristCameras, OracleManipulationPrimitives, OracleGeometry, RGBBackend):
    mode = "oracle_3d_waypoints"
    native_motor_execution = True

    def _create_environment(self, config):
        from .native_joint_limits import enabled, create_unbounded_environment
        if enabled():
            return create_unbounded_environment(self.og, config)
        return super()._create_environment(config)

    def __init__(self, *args, grasping_mode="assisted", waypoint_max_steps=180,
                 ik_backend="custom_dls", wrist_cameras=True, oracle_transport_policy="native_only", **kwargs):
        if grasping_mode not in {"assisted", "physical"}:
            raise ValueError("Unknown native grasping mode")
        if ik_backend not in {"custom_dls", "omnigibson"}:
            raise ValueError("Unknown Oracle IK backend")
        if oracle_transport_policy not in {"native_only", "hybrid_legacy_navigation"}:
            raise ValueError("Unknown Oracle transport policy")
        self.oracle_transport_policy = oracle_transport_policy
        self.ik_backend = ik_backend
        self.wrist_cameras_enabled = bool(wrist_cameras)
        self._official_ik = None
        self.oracle_grasping_mode = grasping_mode
        self.waypoint_max_steps = waypoint_max_steps
        self._trace_target = None
        self._trace_arm = None
        self._trace_waypoint = None
        self._motor_ready = False
        self._navigation_active = False
        self._primitive_context = None
        self._verified_grasps = {}
        super().__init__(*args, **kwargs)
        self.ideal_carry = self.demo_motion = self.local_manipulation = False
        from .native_joint_limits import enabled as unbounded_positions, disable_native_joint_position_limits
        self._native_joint_limit_policy = (disable_native_joint_position_limits(self.robot)
            if unbounded_positions() else dict(status='unchanged'))
        (self.output / 'native_joint_limit_policy.json').write_text(
            json.dumps(self._native_joint_limit_policy, indent=2, allow_nan=False))
        self._command_q = self.robot.get_joint_positions().clone()
        native_held = (getattr(self.robot, "_ag_obj_in_hand", {}) or {})
        self._gripper_commands = {arm: -1.0 if native_held.get(arm) is not None else 1.0
                                  for arm in self.robot.arm_names}
        self._ik_layout = self._build_ik_layout()
        if self.ik_backend == "omnigibson":
            from .oracle_official_ik import OfficialIKAdapter
            self._official_ik = OfficialIKAdapter(self.robot)
        self._traced_object_joints = task_joint_handles(self.env.task.object_scope)
        self._motor_ready = True
        (self.output / "waypoint_trace.jsonl").touch(exist_ok=False)

    def _config(self, *args, **kwargs):
        config = super()._config(*args, **kwargs)
        self.ideal_carry = self.demo_motion = self.local_manipulation = False

        def native_robot(robot):
            robot["include_sensor_names"] = (
                ["left_realsense_link", "right_realsense_link"]
                if self.wrist_cameras_enabled else [])
            robot.pop("exclude_sensor_names", None)
            robot.update(grasping_mode=self.oracle_grasping_mode, disable_grasp_handling=False,
                         action_normalize=False)
            controllers = robot["controller_config"]
            controllers["base"] = {
                "name": "HolonomicBaseJointController", "motor_type": "velocity",
                "command_input_limits": None, "command_output_limits": None,
                "use_impedances": False,
            }
            for name in ("trunk", "arm_left", "arm_right"):
                controllers[name] = {
                    "name": "JointController", "motor_type": "position",
                    "command_input_limits": None, "command_output_limits": None,
                    "use_delta_commands": False, "use_impedances": False,
                }
            for name in ("gripper_left", "gripper_right"):
                controllers[name] = {
                    "name": "MultiFingerGripperController", "mode": "binary",
                    "command_input_limits": [-1, 1],
                }

        for robot in config["robots"]:
            native_robot(robot)
        scene = Path(config["scene"]["scene_file"])
        data = json.loads(scene.read_text())
        embedded = [entry for entry in data["objects_info"]["init_info"].values()
                    if entry["class_name"] == "Robot"]
        for entry in embedded:
            native_robot(entry["args"])
        if embedded:
            encoded = json.dumps(data, sort_keys=True)
            digest = hashlib.sha256(encoded.encode()).hexdigest()
            patched = scene.with_name(digest + ".json")
            patched.write_text(encoded, encoding="utf-8")
            config["scene"]["scene_file"] = str(patched)
            self.input_hashes["configured_scene"] = digest
        config["task"]["termination_config"]["max_steps"] = DEADLINE_STEP_SENTINEL
        return config

    def execute_visual(self, primitive, target, max_steps, **kwargs):
        """Allow only the original navigation branch, never semantic manipulation."""
        if primitive != "navigate_to" or not self._navigation_active:
            raise SkillError("unsupported_operation", "Only navigation is delegated to the original executor")
        return RGBBackend.execute_visual(self, primitive, target, max_steps, **kwargs)

    def _step(self, action):
        """Ideal projection is confined to the explicitly delegated navigation phase."""
        self.deadline.check(changed=True)
        navigation = getattr(self, "_navigation_active", False)
        if navigation and not getattr(self, "_native_transport_active", False):
            if self._base_target is not None:
                self._restore_base_target()
            # The old follower emits q_to_action. Preserve native binary gripper
            # intent and absolute joint targets with this backend's hold action.
            # Native transport already supplies that hold action plus the base
            # velocity; replacing it here would zero every base command.
            action = self._hold_action()
        with component(self, "physics_and_metrics"):
            OmniGibsonBackend._step(self, action)
        if navigation and self._base_target is not None:
            self._restore_base_target()
        if self._motor_ready:
            with component(self, "primitive_tick"):
                self._primitive_tick()
            with component(self, "motor_trace"):
                self._record_motor_step()
        self._video_frame("env_step")

    @staticmethod
    def _list(value):
        return value.detach().cpu().tolist()

    def _relative(self, pose, reference=None):
        import omnigibson.utils.transform_utils as T
        reference = reference or self.robot.get_position_orientation()
        return T.relative_pose_transform(*pose, *reference)

    def _pose(self, pose):
        return {"position_m": self._list(pose[0]), "quaternion_xyzw": self._list(pose[1])}

    def _held(self, arm):
        obj = (getattr(self.robot, "_ag_obj_in_hand", {}) or {}).get(arm)
        return None if obj is None else obj.name

    def _get_held(self):
        held = (getattr(self.robot, "_ag_obj_in_hand", {}) or {})
        return next((obj for obj in held.values() if obj is not None), None)

    def _indices(self, group):
        return (self.robot.trunk_control_idx if group == "trunk"
                else self.robot.arm_control_idx[group.removeprefix("arm_")])

    def _hold_action(self):
        action = self.torch.zeros(self.robot.action_dim, dtype=self._command_q.dtype,
                                  device=self._command_q.device)
        for group in ("trunk", "arm_left", "arm_right"):
            indices = self._indices(group)
            target = self._command_q[indices]
            if getattr(self, '_native_joint_limit_policy', {}).get('status') != 'disabled':
                target = target.clamp(self.robot.joint_lower_limits[indices],
                                      self.robot.joint_upper_limits[indices])
            action[self.robot.controller_action_idx[group]] = target
        for arm in self.robot.arm_names:
            action[self.robot.controller_action_idx["gripper_" + arm]] = self._gripper_commands[arm]
        return action

    def _eef(self, arm):
        return self.robot.get_relative_eef_pose(arm=arm)

    def _record_motor_step(self):
        arms = {}
        for arm in self.robot.arm_names:
            held = (getattr(self.robot, "_ag_obj_in_hand", {}) or {}).get(arm)
            arms[arm] = {"actual": self._pose(self._eef(arm)), "held_object": self._held(arm),
                         "held_object_pose_world": None if held is None else self._pose(held.get_position_orientation()),
                         "gripper_command": self._gripper_commands[arm]}
        recorded_at = now()
        with component(self, "trace_joint_readback"):
            positions = self._list(self.robot.get_joint_positions())
            snapshots = {getattr(self.robot, "name", None): positions}
            object_joints = read_joint_positions(self._traced_object_joints, snapshots=snapshots)
            group_positions = {
                group: [positions[index] for index in self._list(self._indices(group))]
                for group in ("trunk", "arm_left", "arm_right")}
        row = {"at": recorded_at, "sim_step": self.steps,
               "phase": "legacy_navigation" if self._navigation_active else "native_manipulation",
               "request_id": getattr(self, "_active_request_id", None),
               "arm": self._trace_arm, "waypoint_index": self._trace_waypoint,
               "target": self._trace_target, "arms": arms,
               "ik_backend": self.ik_backend,
               "object_joint_positions": object_joints,
               "body_posture": self._body_posture_state(),
               "joint_positions_rad": group_positions,
               "base_pose_world": self._pose(self.robot.get_position_orientation())}
        context = getattr(self, "_primitive_context", None)
        if context:
            row["primitive"] = {"operation": context["operation"], "phase": context["phase"],
                "target_object_id": context["object"].name,
                "target_pose_world": self._pose(context["object"].get_position_orientation()),
                "target_link": None if context["link"] is None else self._link_key(context["object"], context["link"]),
                "contact_seen": context["contact_seen"]}
        if getattr(self, "_motor_trace_writer", None) is None:
            self._motor_trace_writer = LineWriter(self.output / "waypoint_trace.jsonl")
        with component(self, "motor_trace_write"):
            self._motor_trace_writer.write(json.dumps(row, allow_nan=False) + "\n")

    def close(self):
        try:
            writer = getattr(self, "_motor_trace_writer", None)
            if writer is not None:
                writer.close()
        finally:
            super().close()

    def _box(self, obj, reference):
        import omnigibson.utils.transform_utils as T
        low, high = obj.aabb
        corners = self.torch.stack([self.torch.stack([low[i] if bit[i] == 0 else high[i]
                                                    for i in range(3)])
                                    for bit in itertools.product((0, 1), repeat=3)])
        local = (corners - reference[0]) @ T.quat2mat(reference[1])
        minimum, maximum = local.amin(dim=0), local.amax(dim=0)
        return {"min_m": self._list(minimum), "max_m": self._list(maximum),
                "center_m": self._list((minimum + maximum) / 2),
                "size_m": self._list(maximum - minimum)}

    def _body_posture_state(self):
        from .oracle_posture import body_posture_metrics
        base = self.robot.get_position_orientation()
        upper = self.robot.links["torso_link4"].get_position_orientation()
        pose = self._pose(upper)
        return {"link": "torso_link4", "upper_body_pose_world": pose,
                "vertical_axis_local": [0.,0.,1.],
                **body_posture_metrics(self._list(base[0]), pose["position_m"],
                                       pose["quaternion_xyzw"])}

    def _robot_state(self):
        base = self.robot.get_position_orientation()
        arms = {}
        for arm in self.robot.arm_names:
            indices = self.robot.arm_control_idx[arm]
            eef_world = self.robot.eef_links[arm].get_position_orientation()
            fingers = []
            for link in self.robot.finger_links[arm]:
                box = self._box(link, eef_world)
                fingers.append({"link": link.name, "link_id": link.prim_path.rsplit("/", 1)[-1],
                                "prim_path": link.prim_path, "aabb_in_eef": box})
            arms[arm] = {
                "eef_link": self.robot.eef_link_names[arm],
                "eef_pose": self._pose(self._eef(arm)),
                "joint_positions_rad": self._list(self.robot.get_joint_positions()[indices]),
                "joint_limits_rad": self._reported_joint_limits(indices),
                "ready_joint_positions_rad": self._list(self.robot.untucked_default_joint_pos[indices]),
                "gripper_joint_positions": self._list(
                    self.robot.get_joint_positions()[self.robot.gripper_control_idx[arm]]),
                "gripper_command": self._gripper_commands[arm],
                "held_object": self._held(arm), "native_grasp": self._grasp_state(arm),
                "contacts": self._contacts(arm), "finger_geometry": fingers,
            }
        body_links = []
        for name, link in self.robot.links.items():
            if getattr(link, "is_meta_link", False) or link.visual_boundary_points_world is None:
                continue
            box = self._box(link, base)
            if all(math.isfinite(value) for side in ("min_m", "max_m") for value in box[side]) and max(box["size_m"]) > 1e-4:
                body_links.append({"link": name, "aabb": box})
        indices = self.robot.trunk_control_idx
        return {"base_pose_world": self._pose(base), "arms": arms,
                "body_links": body_links,
                "body_geometry_note": "Conservative current link AABBs, for self-clearance reasoning; not a collision planner",
                "trunk": {"body_posture": self._body_posture_state(),
                          "joint_positions_rad": self._list(self.robot.get_joint_positions()[indices]),
                          "joint_limits_rad": self._reported_joint_limits(indices)},
                "grasping_mode": self.oracle_grasping_mode}

    def _reported_joint_limits(self, indices):
        if getattr(self, '_native_joint_limit_policy', {}).get('status') == 'disabled':
            return dict(lower=[None] * len(indices), upper=[None] * len(indices), enabled=False)
        return dict(lower=self._list(self.robot.joint_lower_limits[indices]),
                    upper=self._list(self.robot.joint_upper_limits[indices]))

    def observe(self):
        packet = super().observe()
        base = self.robot.get_position_orientation()
        task_names = {}
        for name, obj in self.env.task.object_scope.items():
            if obj is not None:
                task_names.setdefault(id(obj), []).append(str(name))
        selected = []
        for obj in self.env.scene.objects:
            if obj is self.robot or not hasattr(obj, "aabb") or not hasattr(obj, "states"):
                continue
            low, high = obj.aabb
            near = self.torch.maximum(low, self.torch.minimum(base[0], high))
            distance = float(self.torch.linalg.norm(base[0] - near))
            names = task_names.get(id(obj), [])
            if names or distance <= 3:
                selected.append((not bool(names), distance, obj.name, obj, names))
        selected.sort(key=lambda x: x[:3])
        objects = []
        for _, distance, name, obj, names in selected[:120]:
            row = {"id": name, "task_names": names, "category": obj.category,
                   "fixed_base": bool(obj.fixed_base), "distance_m": distance,
                   "pose": self._pose(self._relative(obj.get_position_orientation(), base)),
                   "aabb": self._box(obj, base), "links": []}
            row.update(self._oracle_object_details(obj, base))
            objects.append(row)
        robot_state = self._robot_state()
        robot_state["wrist_cameras"] = self._wrist_camera_metadata(packet["images"])
        packet["oracle_3d"] = {"frame": "robot_base", "units": {"position": "metre", "angle": "radian",
                                   "quaternion": "xyzw"}, "robot": robot_state,
                              "objects": objects,
                              "object_scope": "task-bound objects plus scene AABBs within 3 m; capped at 120",
                              "omitted_objects": max(0, len(selected) - len(objects))}
        return packet

    def control_contract(self):
        contract = {"protocol": "oracle_3d_waypoints_v2", "frame": "robot_base",
                "wrist_cameras": self._wrist_camera_contract(),
                "axes": "+x forward, +y left, +z up", "quaternion": "xyzw",
                "pose_origin": "reported eef_link; finger contact centre may be offset",
                "grasping_mode": self.oracle_grasping_mode,
                "transport_policy": self.oracle_transport_policy,
                "waypoint_max_steps": self.waypoint_max_steps,
                "ik_backend": self.ik_backend,
                "ik_controller": ("OmniGibson InverseKinematicsController.compute_control / J-PARSE"
                                  if self.ik_backend == "omnigibson" else "custom DLS"),
                "ik_step_limiter": {"gain": .35, "pre_gain_max_joint_increment_rad": .04},
                "position_tolerance_m": .008, "orientation_tolerance_rad": .06,
                "collision_avoidance": False, "semantic_state_setters": False,
                "manipulation_object_pose_projection": False, "manipulation_robot_pose_projection": False,
                "navigation": {"tool": "navigate_to", "target": "latest RGB image_ref and normalized point",
                    "executor": "existing_RGBBackend_navigation",
                    "planner": "existing GT collision grid and checked route",
                    "actuator": "original checked kinematic base follower", "max_control_steps_per_call": 700,
                    "robot_posture_projection": True,
                    "held_navigation_allowed": self.oracle_transport_policy == "hybrid_legacy_navigation",
                    "held_object_pose_projection": self.oracle_transport_policy == "hybrid_legacy_navigation"},
                "manipulation": {"executor": ("official_omnigibson_IK_native_joint_and_gripper"
                                 if self.ik_backend == "omnigibson" else "native_DLS_IK_joint_and_gripper"),
                    "symbolic_state_setters": False, "object_pose_projection": False,
                    "robot_pose_projection": False},
                "operations": ["grasp", "place", "translate", "rotate", "press", "release"],
                "operation_motion_source": "model supplied EEF paths only; no grasp/placement sampler",
                "operation_ik": {"allow_trunk": "explicit model permission; other hand and cameras move with torso",
                                 "final_contact_position_tolerance_m": .004, "final_contact_orientation_tolerance_rad": .04,
                                 "transit_position_tolerance_m": .008, "transit_orientation_tolerance_rad": .06},
                "operation_verification": "native link retention, object relation, joint progress, local device state",
                "tool_completion_is_task_success": False}
        from ..geometry_pick_protocol import enabled
        if enabled():
            for key in ('ik_controller', 'ik_step_limiter', 'operation_ik', 'waypoint_max_steps'):
                contract.pop(key, None)
            contract.update(protocol='geometry_pick_v1', collision_avoidance=True,
                collision_model='cuRobo whole-body spheres and scene collision meshes; target-finger pair exception during final approach',
                operations=['pick'], operation_motion_source='backend generated geometry candidates and timed joint trajectories',
                operation_verification='native assisted retention, measured lift and continuous hold; independent evaluator separate')
            contract['manipulation']['executor'] = 'cuRobo timed joint trajectory via native position controllers'
        from ..model_pose_protocol import (enabled as pose_enabled, ENGINE,
            POSITION_TOLERANCE_M, ORIENTATION_TOLERANCE_RAD)
        if pose_enabled():
            timing_policy = os.environ.get('MAS_MANIP_TIMING', 'model_authored_v1')
            for key in ('ik_backend', 'ik_controller', 'ik_step_limiter', 'operation_ik', 'waypoint_max_steps'):
                contract.pop(key, None)
            contract.update(protocol=ENGINE, collision_avoidance=True,
                collision_model='cuRobo robot spheres, scene meshes and full-scale measured held free objects; explicit target-finger pair exception',
                operations=['model_authored_eef_goal'],
                operation_motion_source='model EEF goal; cuRobo timed joint trajectory',
                operation_verification='endpoint pose and requested native grip/release; independent evaluator separate',
                position_tolerance_m=POSITION_TOLERANCE_M,
                orientation_tolerance_rad=ORIENTATION_TOLERANCE_RAD,
                execution_mode='open_loop_rollback',
                planned_dofs='EEF goal: torso and selected arm, base/other arm locked; explicit arm=both joint goal: torso and both arms, base locked',
                joint_position_limits=('planning/execution checks disabled; native state reported separately'
                    if timing_policy == 'model_authored_v1' else 'native_robot_model; no extra joint-angle ranges'),
                joint_rate_limit_checks=timing_policy != 'model_authored_v1',
                native_mechanical_limits='unchanged',
                extra_body_posture_constraints=False,
                trajectory_max_control_steps=(None if timing_policy in ('model_estimated_v1', 'model_authored_v1') else 100), gripper_control_steps=30,
                trajectory_budget_policy=(
                    'model_authored_v1: model chooses duration without joint-limit rejection or rate extension; no fixed cap; dense collision checks remain'
                    if timing_policy == 'model_authored_v1' else
                    timing_policy + ': historical timing policy; dense collision checks remain'),
                gripper_timing='after reaching goal', automatic_goal_generation=False)
            contract['manipulation']['executor'] = 'cuRobo timed joint trajectory via native position controllers'
            contract['navigation']['held_navigation_allowed'] = False
            contract['navigation']['held_object_pose_projection'] = False
        from ..manip_programs import enabled as program_enabled, ENGINE as PROGRAM_ENGINE
        if program_enabled():
            contract.update(protocol=PROGRAM_ENGINE, goal_source='model_pose_or_selected_or_authored_program',
                operation_motion_source='model goal OR selected existing/authored geometry program -> phase EEF goals or alternative candidates -> cuRobo',
                rollback_scope='entire Manip, including already executed phases')
        if pose_enabled():
            contract['manipulation']['whole_body_recovery'] = dict(
                input='arm=both; goal.joint_positions_rad=all18 torso and both arm joint targets',
                planner='cuRobo plan_single_js; base and gripper joints locked at measured state',
                gripper='hold only; both actual payloads included at full scale',
                verification='all18 joints within0.05rad, unchanged gripper commands and native retention')
        from .native_transport import enabled as native_transport_enabled
        if native_transport_enabled():
            contract['navigation'].update(held_navigation_allowed=True,
                held_transport_executor='native holonomic base velocity controller; native assisted constraint carries payload',
                held_object_pose_projection=False, held_robot_pose_projection=False,
                held_posture_projection=False, held_speed_m_s=.25, held_yaw_rate_rad_s=.4,
                held_arm_motion='hold existing joint targets; no arm replanning during navigate_to',
                native_transport_opt_in='MAS_NATIVE_TRANSPORT=native_joint_v1')
        native_policy = getattr(self, '_native_joint_limit_policy', dict(status='unchanged'))
        contract['native_joint_position_policy'] = native_policy
        if native_policy['status'] == 'disabled':
            contract.update(native_mechanical_limits='disabled_torso_and_both_arms',
                joint_position_limits='planning, execution and native torso/arm position limits disabled')
        return contract

    def _build_ik_layout(self):
        paths = self.robot._articulation_view._physics_view.link_paths[0]
        names = [path.rsplit("/", 1)[-1] for path in paths]
        jac = self.robot.get_relative_jacobian()
        offset = jac.shape[-1] - len(self.robot.get_joint_positions())
        if offset not in (0, 6):
            raise SkillError("ik_geometry_unavailable", "Unrecognized Jacobian joint-column layout")
        layout = {}
        for arm in self.robot.arm_names:
            name = self.robot.eef_links[arm].prim_path.rsplit("/", 1)[-1]
            body = names.index(name)
            layout[arm] = {"body_index": body, "link_count": len(names),
                           "jacobian_rows": jac.shape[0],
                           "jacobian_row": jacobian_row_index(body, len(names), jac.shape[0]),
                           "joint_column_offset": offset}
        (self.output / "kinematic_layout.json").write_text(json.dumps(layout, indent=2))
        return layout

    def _move_eef(self, arm, waypoint):
        import omnigibson.utils.transform_utils as T
        th, robot = self.torch, self.robot
        indices = robot.arm_control_idx[arm]
        if (getattr(self, "_primitive_context", None) or {}).get("allow_trunk"):
            indices = th.cat((robot.trunk_control_idx, indices))
        from ..oracle_operations import waypoint_tolerances
        context = getattr(self, "_primitive_context", None) or {}
        position_tolerance, orientation_tolerance = waypoint_tolerances(context.get("phase"),
            self._trace_waypoint, getattr(self, "_trace_waypoint_count", 1))
        row = self._ik_layout[arm]["jacobian_row"]
        goal_pos = th.as_tensor(waypoint["position_m"], dtype=th.float32,
                                device=self._command_q.device)
        goal_quat = th.as_tensor(waypoint["quaternion_xyzw"], dtype=th.float32,
                                 device=self._command_q.device)
        command = robot.get_joint_positions()[indices].clone()
        lower, upper = robot.joint_lower_limits[indices], robot.joint_upper_limits[indices]
        settled = 0
        start = self.steps

        def error():
            pos, quat = self._eef(arm)
            angular = th.as_tensor(rotation_error_vector(self._list(goal_quat), self._list(quat)),
                                   dtype=goal_pos.dtype, device=goal_pos.device)
            return goal_pos - pos, angular

        for _ in range(self.waypoint_max_steps):
            linear, angular = error()
            if not bool(th.isfinite(linear).all() and th.isfinite(angular).all()):
                raise SkillError("physics_instability", "Non-finite end-effector state", changed=True)
            if float(linear.norm()) <= position_tolerance and float(angular.norm()) <= orientation_tolerance:
                settled += 1
                if settled >= 3:
                    break
            else:
                settled = 0
            jacobian = robot.get_relative_jacobian()
            offset = jacobian.shape[-1] - len(robot.get_joint_positions())
            if row < 0 or row >= jacobian.shape[0] or offset not in (0, 6):
                raise SkillError("ik_geometry_unavailable", "Robot Jacobian layout mismatch")
            measured = robot.get_joint_positions()[indices]
            if self.ik_backend == "omnigibson":
                official_target = self._official_ik.target(arm, indices, goal_pos, goal_quat)
                delta = official_target.to(measured) - measured
            else:
                J = jacobian[row][:, indices + offset].double()
                residual = th.cat((linear, angular)).double()
                delta = J.T @ th.linalg.solve(J @ J.T + .05**2 * th.eye(
                    6, dtype=J.dtype, device=J.device), residual)
            # Use measured-state feedback, not an accumulated command integrator:
            # a delayed position actuator otherwise winds up and overshoots on reversal.
            command = (measured + .35 * delta.to(measured).clamp(-.04, .04)).clamp(lower, upper)
            self._command_q[indices] = command
            self._step(self._hold_action())
        linear, angular = error()
        return {"reached": settled >= 3 and float(linear.norm()) <= position_tolerance and float(angular.norm()) <= orientation_tolerance,
                "settled_control_samples": settled, "ik_backend": self.ik_backend,
                "position_error_m": float(linear.norm()), "orientation_error_rad": float(angular.norm()),
                "actual": self._pose(self._eef(arm)), "target": waypoint,
                "sim_steps": self.steps - start}

    def _set_gripper(self, arm, command):
        self._gripper_commands[arm] = -1.0 if command == "close" else 1.0
        for _ in range(30):
            self._step(self._hold_action())
        return {"reached": True, "command": command, "held_object": self._held(arm),
                "verification": "gripper_command_executed; retention is separate",
                "joint_positions": self._list(
                    self.robot.get_joint_positions()[self.robot.gripper_control_idx[arm]])}

    def execute_motor(self, name, **arguments):
        self.deadline.check()
        if name == 'execute_model_phases':
            from ..manip_programs import enabled as program_enabled
            if not program_enabled():
                raise SkillError('unsupported_operation', 'Model geometry programs are not enabled')
            with action_timing(self, 'manip'):
                try:
                    if isinstance(arguments.get('phases'), dict):
                        from .oracle_candidates import ModelCandidateManip
                        return ModelCandidateManip(self).execute_candidates(
                            arguments['phases'], program=arguments.get('program'))
                    from .oracle_pose import ModelPoseManip
                    return ModelPoseManip(self).execute_phases(**arguments)
                except SkillError:
                    raise
                except Exception as exc:
                    raise SkillError('model_program_backend_failed',
                                     f'{type(exc).__name__}: {str(exc)[:500]}') from exc
        if name == 'manip':
            from ..model_pose_protocol import enabled as pose_enabled
            if pose_enabled():
                with action_timing(self, name):
                    try:
                        from .oracle_pose import ModelPoseManip
                        return ModelPoseManip(self).execute(**arguments)
                    except SkillError:
                        raise
                    except Exception as exc:
                        raise SkillError('model_pose_backend_failed',
                                         f'{type(exc).__name__}: {str(exc)[:500]}') from exc
            from ..geometry_pick_protocol import enabled
            if not enabled():
                raise SkillError('unsupported_operation', 'Intentful Manip is not enabled in this episode')
            with action_timing(self, name):
                from ..pick_engine import VersionedGeometryPick as GeometryPick
                return GeometryPick(self).execute(**arguments)
        if name in {"grasp", "place", "translate", "rotate", "press", "release"}:
            return self.execute_primitive(name, **arguments)
        if name == "execute_waypoints":
            if arguments["frame"] != "robot_base":
                raise SkillError("invalid_arguments", "Only current robot_base coordinates are supported")
            # Validate the WHOLE batch before the first waypoint can change physics.
            waypoints = [checked_pose(pose) for pose in arguments["waypoints"]]
            arm, results = arguments["arm"], []
            self._trace_arm = arm
            self._trace_waypoint_count = len(waypoints)
            try:
                for index, waypoint in enumerate(waypoints):
                    self._trace_target, self._trace_waypoint = waypoint, index
                    result = self._move_eef(arm, waypoint)
                    result["waypoint_index"] = index
                    results.append(result)
                    if not result["reached"]:
                        return {"reached": False, "failed_waypoint": index, "waypoints": results}
                    if waypoint["gripper"] != "hold":
                        result["gripper_result"] = self._set_gripper(arm, waypoint["gripper"])
                return {"reached": True, "waypoints": results}
            finally:
                self._trace_target = self._trace_arm = self._trace_waypoint = None
                self._trace_waypoint_count = 0
        if name == "set_gripper":
            return self._set_gripper(arguments["arm"], arguments["command"])
        if name == "wait":
            for _ in range(arguments["steps"]):
                self._step(self._hold_action())
            return {"reached": True, "steps": arguments["steps"]}
        if name == "move_joints":
            return self._move_joints(arguments["group"], arguments["positions_rad"])
        if name == "navigate_to":
            return self._navigate_to(arguments["target"])
        raise SkillError("unsupported_operation", "Unknown Oracle motor tool")

    def _move_joints(self, group, positions_rad):
        indices = self._indices(group)
        if len(positions_rad) != len(indices):
            raise SkillError("invalid_arguments", f"{group} needs {len(indices)} joint positions")
        target = self.torch.as_tensor(positions_rad, dtype=self._command_q.dtype,
                                      device=self._command_q.device)
        lower, upper = self.robot.joint_lower_limits[indices], self.robot.joint_upper_limits[indices]
        if not bool(self.torch.isfinite(target).all()) or bool(((target < lower) | (target > upper)).any()):
            raise SkillError("invalid_arguments", "Joint target is non-finite or outside reported limits")
        start = self.robot.get_joint_positions()[indices].clone()
        count = max(1, math.ceil(float((target - start).abs().max()) / .025))
        for step in range(1, count + 1):
            phase = step / count
            phase = phase * phase * (3 - 2 * phase)
            self._command_q[indices] = start + phase * (target - start)
            self._step(self._hold_action())
        for _ in range(20):
            self._step(self._hold_action())
        measured = self.robot.get_joint_positions()[indices]
        error = float((target - measured).abs().max())
        return {"reached": error <= .10, "max_joint_error_rad": error,
                "actual_positions_rad": self._list(measured)}

    def _navigation_hold_action(self):
        # q_to_action rejects binary gripper controllers. Keep native intent.
        return self._hold_action()

    def _navigate_to(self, target):
        """Wrap the existing visual navigate_to; do not reimplement route planning."""
        if self._navigation_active:
            raise SkillError("navigation_busy", "Navigation is already active")
        held = self._get_held()
        from ..model_pose_protocol import enabled as pose_enabled
        from .native_transport import enabled as native_transport_enabled
        native_carry = held is not None and native_transport_enabled()
        if held is not None and not native_carry and (pose_enabled() or self.oracle_transport_policy != "hybrid_legacy_navigation"):
            raise SkillError("held_navigation_disabled", "native_only forbids held-object navigation; use native local manipulation")
        self._navigation_active = True
        try:
            if native_carry:
                from .native_transport import run
                return run(self, target)
            details = self.execute_visual("navigate_to", target, max_steps=700)
            return {"reached": True, **details,
                    "execution_phase": "legacy_navigation",
                    "transport_policy": self.oracle_transport_policy,
                    "transport_execution": "ideal_held_object_projection" if held is not None else "empty_robot_navigation",
                    "manipulation_performed": False}
        finally:
            self._navigation_active = False
            self._base_target = None
            # Navigation holds a measured posture. Resume native control from
            # that actual state rather than stale pre-navigation servo targets.
            self._command_q = self.robot.get_joint_positions().clone()

    def evaluate(self):
        result = OmniGibsonBackend.evaluate(self)
        result.update(protocol="oracle_3d_waypoints_v2", observation_mode=self.mode,
                      inside_placement="not_applicable_native_waypoints",
                      time_metric_scope="native env.step only",
                      grasping_mode=self.oracle_grasping_mode,
                      base_distance_m=result.pop("ideal_navigation_distance_m", 0),
                      execution="existing_harness_navigation; native_manipulation_IK_and_contact")
        return result

    def provenance(self):
        result = OmniGibsonBackend.provenance(self)
        result.update(executor="existing_navigation_plus_oracle_waypoint_native_manipulation",
                      inside_placement="not_applicable_native_waypoints",
                      observation_mode=self.mode, model_visible_truth=True,
                      grasping_mode=self.oracle_grasping_mode,
                      controller_contract=self.control_contract(), image_size=self.image_size,
                      robot_camera_views=list(self._rgb_sensors()),
                      wrist_cameras=self._wrist_camera_contract())
        if self._official_ik is not None:
            result["official_ik"] = self._official_ik.provenance()
        from ..geometry_pick_protocol import enabled
        if enabled():
            result.update(executor='geometry_pick_v1', planner='StanfordVL/curobo@78612f45cef52c3fa0298de243a54cd7ca614414',
                grasp_candidates='collision_mesh_cross_sections;RoboTwin_style_contact_frames',
                native_assisted_retention=True)
        snapshot = self.output / "source_snapshot" / "manipulation_agent"
        package = snapshot if snapshot.is_dir() else Path(__file__).resolve().parents[1]
        result["oracle_source_sha256"] = {
            name: hashlib.sha256((package / name).read_bytes()).hexdigest()
            for name in ("oracle_contracts.py", "oracle_harness.py", "oracle_policy.py",
                         "oracle_operations.py", "executors/oracle_waypoints.py",
                          "executors/oracle_geometry.py", "executors/oracle_primitives.py",
                          "executors/oracle_official_ik.py", "executors/oracle_cameras.py",
                          "executors/oracle_posture.py", "executors/native_joint_limits.py")}
        return result
