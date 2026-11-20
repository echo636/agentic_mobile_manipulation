"""Explicit privileged-observation protocol, separate from the RGB tool registry."""
import copy
import math

from .contracts import INT, STRING, SkillError, object_schema

PROFILE = "oracle_waypoints"
ACTION_TOOLS = ("navigate_to", "execute_waypoints", "set_gripper", "move_joints", "wait",
                "grasp", "place", "translate", "rotate", "press", "release", "manip")
ARM = {"type": "string", "enum": ["left", "right"]}


def vector(length):
    return {"type": "array", "minItems": length, "maxItems": length,
            "items": {"type": "number"}}


def tool_specs():
    pose = object_schema({
        "position_m": vector(3),
        "quaternion_xyzw": vector(4),
        "gripper": {"type": "string", "enum": ["hold", "open", "close"]},
    })
    rows = [
        ("initialize", "Read the prepared Oracle 3D episode and robot control contract.", {}),
        ("observe_scene", "Read current Oracle 3D geometry and RGB without advancing physics.", {}),
        ("list_skills", "List this episode's frozen native Oracle manipulation experience documents.", {}),
        ("read_skill", "Read an allowlisted Oracle skill document from the immutable episode snapshot.", {
            "name": STRING, "resource": STRING}),
        ("execute_waypoints", "Track your ordered end-effector poses with closed-loop IK. "
         "All poses are in the CURRENT robot base frame; metres and quaternion xyzw. "
         "The base and trunk hold still. Each gripper command runs only AFTER its pose is reached. "
         "Stops at the first failed waypoint. IK does not plan obstacle avoidance. "
         "reached means pose tracking only, never grasp or task success.", {
             "arm": ARM, "frame": {"type": "string", "enum": ["robot_base"]},
             "waypoints": {"type": "array", "minItems": 1, "maxItems": 16, "items": pose},
             "revision": INT}),
        ("set_gripper", "Open or close one gripper while holding robot joint targets. "
         "Inspect held_object and the new geometry; closing is not a verified grasp.", {
             "arm": ARM, "command": {"type": "string", "enum": ["open", "close"]},
             "revision": INT}),
        ("move_joints", "Move one joint group to absolute joint positions in radians, using "
         "bounded interpolation. Use the reported limits and ready posture. Trunk motion changes "
         "both hands and cameras. This is not collision-aware.", {
             "group": {"type": "string", "enum": ["trunk", "arm_left", "arm_right"]},
             "positions_rad": {"type": "array", "minItems": 4, "maxItems": 7,
                               "items": {"type": "number"}}, "revision": INT}),
        ("navigate_to", "Use the existing harness navigation planner and checked kinematic base follower. "
         "Select a visible target in the latest front/back/left/right surround RGB using image_ref and normalized point [x,y]. Wrist RGB is for manipulation feedback only. "
         "A floor pixel requests a floor location; an elevated object pixel requests a nearby approach. "
         "This reuses the original navigate_to implementation; it does not perform manipulation. "
         "After arrival read the new Oracle geometry before choosing end-effector waypoints. "
         "Navigation may use the original ideal base/posture/held-object pose projection.", {
             "target": object_schema({"image_ref": STRING,
                 "point": {"type": "array", "minItems": 2, "maxItems": 2,
                           "items": {"type": "number", "minimum": 0, "maximum": 1}}}),
             "revision": INT}),
        ("wait", "Advance simulation while holding the current commanded posture and grippers.", {
            "steps": {"type": "integer", "minimum": 1, "maximum": 150}, "revision": INT}),
        ("finish", "Close the episode. Independent evaluation decides whole-task success.", {
            "outcome": {"type": "string", "enum": ["achieved", "blocked", "aborted"]},
            "reason": STRING}),
    ]
    result = []
    for name, description, properties in rows:
        action = name in ACTION_TOOLS
        result.append({"name": name, "description": description,
                       "inputSchema": object_schema(properties),
                       "annotations": {"readOnlyHint": name in {"initialize", "observe_scene", "list_skills", "read_skill"},
                                       "destructiveHint": action or name == "finish",
                                       "idempotentHint": name == "initialize", "openWorldHint": False},
                       "_meta": {"mas": {"profile": PROFILE, "mutates_world": action,
                                         "requires_fresh_observation": action,
                                         "closes_episode": name == "finish"}}})
    from .tools import REGISTRY
    notes = REGISTRY["task_notes"]
    result.append({"name": notes.name, "description": notes.description,
                   "inputSchema": copy.deepcopy(notes.schema),
                   "annotations": notes.metadata.annotations(),
                   "_meta": {"mas": {"profile": PROFILE, **notes.metadata.as_dict()}}})
    from .oracle_operations import operation_specs
    from .manip_runtime import tool_specs as manip_specs
    recipes = manip_specs()
    for row in recipes:
        row['_meta'] = {'mas': {'profile': PROFILE, 'mutates_world': row['name'] == 'manip',
                                'requires_fresh_observation': row['name'] == 'manip',
                                'closes_episode': False, 'motion_source': 'manip_recipe'}}
    from .geometry_pick_protocol import enabled, specs as geometry_specs
    from .model_pose_protocol import enabled as pose_enabled, specs as pose_specs
    from .manip_programs import enabled as program_enabled, specs as program_specs
    catalog = result + operation_specs() + recipes
    if program_enabled():
        return program_specs(catalog)
    if pose_enabled():
        return pose_specs(catalog)
    return geometry_specs(catalog) if enabled() else catalog


def checked_pose(waypoint):
    """Validate every target before any physics; return a normalized copy."""
    result = copy.deepcopy(waypoint)
    for key, length in (("position_m", 3), ("quaternion_xyzw", 4)):
        value = result.get(key)
        if not isinstance(value, list) or len(value) != length or any(
                type(x) not in (int, float) or not math.isfinite(x) for x in value):
            raise SkillError("invalid_arguments", f"{key} needs {length} finite numbers")
    if math.sqrt(sum(x*x for x in result["position_m"])) > 3:
        raise SkillError("invalid_arguments", "End-effector target exceeds the 3 m base-frame bound")
    quat = result["quaternion_xyzw"]
    norm = math.sqrt(sum(x*x for x in quat))
    if abs(norm - 1) > .02:
        raise SkillError("invalid_arguments", "quaternion_xyzw must be unit length (tolerance 0.02)")
    if result.get("gripper") not in {"hold", "open", "close"}:
        raise SkillError("invalid_arguments", "Unknown waypoint gripper command")
    result["quaternion_xyzw"] = [x / norm for x in quat]
    return result


def public_observation(value, revision):
    """Privileged contract only; the original RGB allowlist remains unchanged."""
    if set(value) != {"images", "observation_mode", "capture", "oracle_3d"}:
        raise RuntimeError("Oracle observation top-level contract violation")
    if value["observation_mode"] != "oracle_3d_waypoints":
        raise RuntimeError("Expected the explicit Oracle 3D observation mode")
    state = value["oracle_3d"]
    if set(state) != {"frame", "units", "robot", "objects", "object_scope", "omitted_objects"}:
        raise RuntimeError("Oracle state contract violation")
    # Reject accidental evaluator state anywhere in the model-facing packet.
    forbidden = {"goal_options", "ground_goal_state_options", "official_metrics", "task_success",
                 "goal_satisfaction_fraction", "initial_goal_options"}
    def check(item):
        if isinstance(item, dict):
            if forbidden.intersection(item):
                raise RuntimeError("Evaluator state is not an Oracle geometry observation")
            for child in item.values():
                check(child)
        elif isinstance(item, list):
            for child in item:
                check(child)
        elif isinstance(item, float) and not math.isfinite(item):
            raise RuntimeError("Non-finite Oracle observation")
    check(value)
    return {**copy.deepcopy(value), "revision": revision}


def jacobian_row_index(body_index, link_count, jacobian_rows):
    """PhysX omits the root row for fixed bases, includes it for floating bases."""
    if jacobian_rows == link_count:
        return body_index
    if jacobian_rows == link_count - 1 and body_index > 0:
        return body_index - 1
    raise SkillError("ik_geometry_unavailable", "Unrecognized articulation Jacobian link layout")


def rotation_error_vector(desired, current):
    """Shortest rotation current->desired, expressed in their shared frame."""
    dx, dy, dz, dw = desired
    cx, cy, cz, cw = current
    # desired * conjugate(current), xyzw
    vector = [cw*dx - dw*cx - (dy*cz - dz*cy),
              cw*dy - dw*cy - (dz*cx - dx*cz),
              cw*dz - dw*cz - (dx*cy - dy*cx)]
    scalar = dw*cw + dx*cx + dy*cy + dz*cz
    if scalar < 0:
        vector, scalar = [-x for x in vector], -scalar
    length = math.sqrt(sum(x*x for x in vector))
    if length < 1e-10:
        return [0., 0., 0.]
    scale = 2 * math.atan2(length, max(0., scalar)) / length
    return [x * scale for x in vector]
