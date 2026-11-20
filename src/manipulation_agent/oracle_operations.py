"""Strict model-authored manipulation operations; CPU-only schemas and checks."""
import math
from .contracts import INT, STRING, SkillError, object_schema
from .oracle_contracts import checked_pose

NAMES = ("grasp", "place", "translate", "rotate", "press", "release")


def operation_specs():
    pose = object_schema({"position_m": _vector(3), "quaternion_xyzw": _vector(4),
                          "gripper": {"type": "string", "enum": ["hold"]}})
    path = {"type": "array", "minItems": 1, "maxItems": 32, "items": pose}
    optional_path = {**path, "minItems": 0}
    common = {"arm": {"type": "string", "enum": ["left", "right"]},
              "frame": {"type": "string", "enum": ["robot_base"]},
              "allow_trunk": {"type": "boolean"}, "revision": INT}
    steps = {"type": "integer", "minimum": 1, "maximum": 150}
    target = {"target_object_id": STRING, "target_link": STRING}
    descriptions = {
        "grasp": "Execute YOUR pregrasp, approach, close and verification paths. Never samples a grasp. "
                 "All paths are EEF LINK poses with gripper=hold. For free objects provide a lift path; "
                 "for articulated handles provide a small joint-consistent probe, not an upward lift.",
        "place": "Place an already retained object using YOUR placement and retreat paths. Opens only "
                 "after reaching the release pose, then checks actual Inside/OnTop and stability.",
        "translate": "Follow YOUR path to move the specified prismatic joint by displacement_m. "
                     "Uses native contact only; never sets joint state. Read the joint axis and limits first.",
        "rotate": "Follow YOUR arc path to move the specified revolute joint by angle_rad. "
                  "Uses native contact only; never sets joint state. Read the hinge axis and limits first.",
        "press": "Follow YOUR press/dwell/retreat paths with an eligible finger contacting the supplied "
                 "functional toggle region. Verifies the native local device state; never sets it.",
        "release": "Open the native gripper, settle, and verify disengagement from the specified target. "
                   "Release alone is not successful placement.",
    }
    properties = {
        "grasp": {**target, "grasp_kind": {"type": "string", "enum": ["free_object", "articulated_handle"]},
                  "pregrasp_waypoints": path, "approach_waypoints": path,
                  "verification_waypoints": path, "settle_steps": steps,
                  "min_lift_m": {"type": "number", "minimum": .01, "maximum": .3}},
        "place": {"target_object_id": STRING, "destination_object_id": STRING,
                  "relation": {"type": "string", "enum": ["inside", "on_top"]},
                  "placement_waypoints": path, "retreat_waypoints": optional_path, "settle_steps": steps},
        "translate": {**target, "joint_id": STRING,
                      "displacement_m": {"type": "number", "minimum": -.5, "maximum": .5},
                      "motion_waypoints": path, "release_after": {"type": "boolean"},
                      "settle_steps": steps},
        "rotate": {**target, "joint_id": STRING,
                   "angle_rad": {"type": "number", "minimum": -3.14, "maximum": 3.14},
                   "motion_waypoints": path, "release_after": {"type": "boolean"},
                   "settle_steps": steps},
        "press": {"target_object_id": STRING, "functional_region": STRING, "contact_link": STRING,
                  "press_waypoints": path, "dwell_steps": steps, "retreat_waypoints": path,
                  "desired_local_state": {"type": "boolean"}},
        "release": {"target_object_id": STRING, "settle_steps": steps},
    }
    return [{"name": name, "description": descriptions[name],
             "inputSchema": object_schema({**common, **properties[name]}),
             "annotations": {"readOnlyHint": False, "destructiveHint": True,
                             "idempotentHint": False, "openWorldHint": False},
             "_meta": {"mas": {"profile": "oracle_waypoints", "mutates_world": True,
                               "requires_fresh_observation": True, "closes_episode": False,
                               "model_authored_motion": True}}} for name in NAMES]


def _vector(size):
    return {"type": "array", "minItems": size, "maxItems": size, "items": {"type": "number"}}


def checked_paths(arguments):
    """Validate the entire operation before even opening a gripper."""
    result = dict(arguments)
    for key, value in arguments.items():
        if key.endswith("_waypoints"):
            minimum = 0 if key == "retreat_waypoints" else 1
            if not isinstance(value, list) or not minimum <= len(value) <= 32:
                raise SkillError("invalid_arguments", "Invalid operation waypoint count")
            result[key] = [checked_pose(row) for row in value]
            if any(row["gripper"] != "hold" for row in result[key]):
                raise SkillError("invalid_arguments", "Operation paths must use gripper=hold; phases control the gripper")
    if result.get("frame") != "robot_base":
        raise SkillError("invalid_arguments", "Use current robot_base coordinates")
    return result


def joint_goal(start, delta, lower, upper, kind):
    if not all(type(x) in (int, float) and math.isfinite(x) for x in (start, delta, lower, upper)):
        raise SkillError("joint_geometry_unavailable", "Finite joint state and limits are required")
    if abs(delta) < (.005 if kind == "prismatic" else .03):
        raise SkillError("invalid_arguments", "Requested joint movement is too small to verify")
    goal = start + delta
    if goal < lower - 1e-5 or goal > upper + 1e-5:
        raise SkillError("joint_limit", "Requested relative movement exceeds the observed joint limits")
    return goal


def waypoint_tolerances(phase, index, count):
    """Transit geometry and final contact geometry have different tracking contracts."""
    final_contact = phase in {"approach", "placement", "press_approach", "joint_motion"} and index == count - 1
    return (.004, .04) if final_contact else (.008, .06)
