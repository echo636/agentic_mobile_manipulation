"""Native contact operations: execute supplied geometry, never generate a hidden plan."""
import json
import math

from ..contracts import SkillError
from ..oracle_operations import checked_paths, joint_goal
from ..records import now


class OracleManipulationPrimitives:
    def _phase(self, name):
        self._primitive_context["phase"] = name
        if getattr(self, "video", None) is not None and not self.video.closed:
            self.video.mark_phase(name)
        with (self.output / "primitive_phases.jsonl").open("a") as stream:
            stream.write(json.dumps({"at": now(), "sim_step": self.steps,
                "request_id": getattr(self, "_active_request_id", None),
                "operation": self._primitive_context["operation"], "phase": name}) + "\n")

    def _primitive_tick(self):
        context = getattr(self, "_primitive_context", None)
        if not context:
            return
        contacts = self._contacts(context["arm"], context["object"], context.get("link"))
        context["contact_seen"] |= bool(contacts.get("pairs"))
        context["contact_links_seen"].update(pair[0].rsplit("/", 1)[-1] for pair in contacts.get("pairs", []))
        context["positions"].append(self._list(context["object"].get_position_orientation()[0]))

    def _path_phase(self, name, arm, path, phases):
        self._phase(name)
        result = self.execute_motor("execute_waypoints", arm=arm, frame="robot_base", waypoints=path)
        phases.append({"phase": name, **result})
        return result["reached"]

    def _settle(self, steps):
        for _ in range(steps):
            self._step(self._hold_action())

    def _stable_object(self, obj, steps):
        positions = []
        for _ in range(steps):
            self._step(self._hold_action())
            positions.append(self._list(obj.get_position_orientation()[0]))
        tail = positions[-min(10, len(positions)):]
        drift = max((math.dist(tail[0], point) for point in tail), default=0.)
        return {"position_drift_m": drift, "stable": len(positions) >= 3 and drift <= .01,
                "samples": len(positions)}

    def _operation_result(self, reached, phases, verified=None, **evidence):
        return {"reached": bool(reached), "verified": verified,
                "verification_status": "verified" if verified is True else "failed" if verified is False else "unknown",
                "phase": self._primitive_context["phase"], "phases": phases,
                "contact_seen": self._primitive_context["contact_seen"], **evidence}

    def _require_retention(self, arm, obj, link=None):
        native = self._grasp_state(arm)
        if link is None and native["object_id"] == obj.name:
            link = self._resolve_link(obj, native["target_link"])
        if link is None:
            record = getattr(self, "_verified_grasps", {}).get(arm)
            if record and record["object_id"] == obj.name:
                link = self._resolve_link(obj, record["target_link"])
        if link is None or not self._retention(arm, obj, link)["retained"]:
            raise SkillError("not_retained", "The specified object/link is not currently retained by this hand")
        return link

    def execute_primitive(self, name, **arguments):
        arguments = checked_paths(arguments)
        arm = arguments["arm"]
        obj = self._resolve_object(arguments["target_object_id"])
        link = self._resolve_link(obj, arguments["target_link"]) if "target_link" in arguments else None
        context = {"operation": name, "arm": arm, "object": obj, "link": link,
                   "phase": "preconditions", "contact_seen": False, "contact_links_seen": set(),
                   "allow_trunk": arguments.get("allow_trunk", False), "positions": []}
        self._primitive_context = context
        try:
            return getattr(self, "_primitive_" + name)(obj=obj, link=link, **arguments)
        finally:
            self._primitive_context = None

    def _release_evidence(self, arm, obj):
        indices = self.robot.gripper_control_idx[arm]
        q = self.robot.get_joint_positions()[indices]
        lower, upper = self.robot.joint_lower_limits[indices], self.robot.joint_upper_limits[indices]
        opening = float(((q - lower) / (upper - lower).clamp_min(1e-6)).mean())
        native = self._grasp_state(arm)
        contacts = self._contacts(arm, obj)
        disengaged = (native["object_id"] != obj.name and self._gripper_commands[arm] > 0
                      and opening >= .8 and contacts.get("finger_count", 0) is not None
                      and contacts.get("finger_count", 0) < 2)
        return {"released": bool(disengaged), "opening_fraction": opening,
                "native_grasp": native, "contacts": contacts}

    def _primitive_grasp(self, obj, link, arm, grasp_kind, pregrasp_waypoints,
                         approach_waypoints, verification_waypoints, settle_steps,
                         min_lift_m, **kwargs):
        phases = []
        if self._grasp_state(arm)["object_id"] not in (None, obj.name):
            raise SkillError("hand_occupied", "Release the other object before grasping this target")
        if grasp_kind == "free_object" and obj.fixed_base:
            raise SkillError("invalid_grasp_kind", "A fixed root is not a free-object lift target")
        if grasp_kind == "articulated_handle" and not self._is_articulated_handle(obj, link):
            raise SkillError("invalid_grasp_kind", "Handle verification requires a link driven by a movable ancestor joint")
        before = self._list(obj.get_position_orientation()[0])
        self._phase("open")
        phases.append({"phase": "open", **self._set_gripper(arm, "open")})
        if not self._path_phase("pregrasp", arm, pregrasp_waypoints, phases):
            return self._operation_result(False, phases, False)
        if not self._path_phase("approach", arm, approach_waypoints, phases):
            return self._operation_result(False, phases, False)
        self._phase("close")
        phases.append({"phase": "close", **self._set_gripper(arm, "close")})
        candidate = self._retention(arm, obj, link)
        if not candidate["retained"]:
            return self._operation_result(True, phases, False, retention=candidate,
                                          reason="No target-link retention; lift/probe was not executed")
        if not self._path_phase("verification_motion", arm, verification_waypoints, phases):
            return self._operation_result(False, phases, False, retention=self._retention(arm, obj, link))
        self._phase("verify_retention")
        relative = self._relative(link.get_position_orientation(), self.robot.eef_links[arm].get_position_orientation())[0]
        drift = 0.
        retained_samples = 0
        for _ in range(settle_steps):
            self._step(self._hold_action())
            current = self._relative(link.get_position_orientation(), self.robot.eef_links[arm].get_position_orientation())[0]
            drift = max(drift, float((current - relative).norm()))
            retained_samples += int(self._retention(arm, obj, link)["retained"])
        after = self._list(obj.get_position_orientation()[0])
        lift = after[2] - before[2]
        retained = retained_samples == settle_steps and drift <= .03
        verified = retained and (grasp_kind != "free_object" or lift >= min_lift_m)
        if verified:
            if not hasattr(self, "_verified_grasps"):
                self._verified_grasps = {}
            self._verified_grasps[arm] = {"object_id": obj.name, "target_link": self._link_key(obj, link)}
        return self._operation_result(True, phases, bool(verified), retention=self._retention(arm, obj, link),
                lifted_m=lift, required_lift_m=min_lift_m if grasp_kind == "free_object" else None,
                retained_samples=retained_samples, samples=settle_steps, relative_position_drift_m=drift,
                grasp_kind=grasp_kind, grasping_mode=self.oracle_grasping_mode)

    def _primitive_place(self, obj, link, arm, destination_object_id, relation,
                         placement_waypoints, retreat_waypoints, settle_steps, **kwargs):
        self._require_retention(arm, obj)
        destination = self._resolve_object(destination_object_id)
        if obj is destination:
            raise SkillError("invalid_destination", "An object cannot be placed inside/on itself")
        phases = []
        if not self._path_phase("placement", arm, placement_waypoints, phases):
            return self._operation_result(False, phases, False)
        self._require_retention(arm, obj)
        self._phase("release")
        phases.append({"phase": "release", **self._set_gripper(arm, "open")})
        if hasattr(self, "_verified_grasps"):
            self._verified_grasps.pop(arm, None)
        retreat_reached = True
        if retreat_waypoints:
            retreat_reached = self._path_phase("retreat", arm, retreat_waypoints, phases)
        self._phase("verify_placement")
        stability = self._stable_object(obj, settle_steps)
        relation_value = self._relation(obj, destination, relation)
        release_evidence = self._release_evidence(arm, obj)
        released = release_evidence["released"]
        verified = None if relation_value is None else bool(released and relation_value and stability["stable"])
        return self._operation_result(retreat_reached, phases, verified, relation=relation,
                relation_satisfied=relation_value, released=released, release_evidence=release_evidence, stability=stability,
                destination_object_id=destination.name)

    def _primitive_translate(self, **arguments):
        return self._joint_operation(kind="prismatic", delta=arguments["displacement_m"], **arguments)

    def _primitive_rotate(self, **arguments):
        return self._joint_operation(kind="revolute", delta=arguments["angle_rad"], **arguments)

    def _joint_operation(self, obj, link, arm, joint_id, motion_waypoints, release_after,
                         settle_steps, kind, delta, **kwargs):
        self._require_retention(arm, obj, link)
        if joint_id not in obj.joints:
            raise SkillError("unknown_joint", "The supplied joint ID is absent")
        joint = obj.joints[joint_id]
        state = self._joint_description(obj, joint_id, joint)
        if state["type"] != kind:
            raise SkillError("wrong_joint_type", "The operation does not match the observed joint type")
        start = state["position"]
        goal = joint_goal(start, delta, state["lower_limit"], state["upper_limit"], kind)
        parent = obj.links[state["parent_link"]]
        parent_before = self._list(parent.get_position_orientation()[0])
        phases = []
        reached = self._path_phase("joint_motion", arm, motion_waypoints, phases)
        self._phase("verify_joint_motion")
        self._settle(settle_steps)
        end = self._joint_description(obj, joint_id, joint)["position"]
        parent_drift = math.dist(parent_before, self._list(parent.get_position_orientation()[0]))
        retention = self._retention(arm, obj, link)
        tolerance = max(.01 if kind == "prismatic" else .02, abs(delta)*.15)
        actual_delta = end - start
        verified = (abs(end - goal) <= tolerance and actual_delta*delta > 0
                    and abs(actual_delta) >= abs(delta)*.5 and parent_drift <= .02
                    and retention["retained"])
        if reached and release_after:
            self._phase("release_handle")
            phases.append({"phase": "release_handle", **self._set_gripper(arm, "open")})
            if hasattr(self, "_verified_grasps"):
                self._verified_grasps.pop(arm, None)
        return self._operation_result(reached, phases, bool(verified), joint_id=joint_id,
                joint_start=start, joint_goal=goal, joint_actual=end, actual_delta=actual_delta,
                joint_error=abs(end-goal), tolerance=tolerance, parent_position_drift_m=parent_drift,
                retention=retention)

    def _primitive_press(self, obj, link, arm, functional_region, contact_link,
                         press_waypoints, dwell_steps, retreat_waypoints,
                         desired_local_state, **kwargs):
        state, requires_closed_and_open = self._native_toggle(obj)
        if state is None or state.link.name != functional_region:
            raise SkillError("unknown_functional_region", "Select an observed native togglebutton region")
        eligible = {}
        for part in self.robot.finger_links[arm]:
            canonical = part.prim_path.rsplit("/", 1)[-1]
            eligible[part.name] = canonical
            eligible[canonical] = canonical
        if contact_link not in eligible:
            raise SkillError("invalid_contact_link", "This native toggle requires a finger of the selected arm")
        canonical_contact_link = eligible[contact_link]
        before = bool(state.get_value())
        if before == desired_local_state:
            return self._operation_result(True, [], True, state_before=before, state_after=before,
                                          already_desired=True, activation="none_needed")
        if requires_closed_and_open:
            raise SkillError("device_requires_closed", "Close this device through native motion before toggling it")
        phases = []
        if not self._path_phase("press_approach", arm, press_waypoints, phases):
            after = bool(state.get_value())
            contact_seen = canonical_contact_link in self._primitive_context["contact_links_seen"]
            return self._operation_result(False, phases, after == desired_local_state and contact_seen,
                state_before=before, state_after=after, desired_local_state=desired_local_state,
                declared_contact_seen=contact_seen,
                verification_note="Native activation and pose tracking are independent; no later path executed")
        self._phase("dwell")
        self._settle(dwell_steps)
        reached = self._path_phase("retreat", arm, retreat_waypoints, phases)
        self._phase("verify_device")
        self._settle(3)
        after = bool(state.get_value())
        return self._operation_result(reached, phases, after == desired_local_state and canonical_contact_link in self._primitive_context["contact_links_seen"],
                declared_contact_seen=canonical_contact_link in self._primitive_context["contact_links_seen"],
                state_before=before, state_after=after, desired_local_state=desired_local_state,
                activation="native_ToggledOn_contact_overlap_update",
                declared_contact_link=contact_link,
                verification_note="Native state transition verifies activation; requested finger contacts are recorded separately")

    def _primitive_release(self, obj, link, arm, settle_steps, **kwargs):
        current = self._grasp_state(arm)["object_id"]
        if current not in (None, obj.name):
            raise SkillError("wrong_release_target", "This hand is retaining a different object")
        phases = []
        self._phase("release")
        phases.append({"phase": "release", **self._set_gripper(arm, "open")})
        self._phase("verify_release")
        self._settle(settle_steps)
        release_evidence = self._release_evidence(arm, obj)
        released = release_evidence["released"]
        if hasattr(self, "_verified_grasps"):
            self._verified_grasps.pop(arm, None)
        return self._operation_result(True, phases, bool(released), released=released,
                release_evidence=release_evidence, verification_note="Disengagement only; placement and stability are separate")
