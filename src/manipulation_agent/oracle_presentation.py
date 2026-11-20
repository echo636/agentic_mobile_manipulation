"""Model-only Oracle projection. Full backend snapshots and motor logs stay intact."""
from copy import deepcopy

ROBOT_SECTIONS = ("base_pose_world", "arms", "body_links", "trunk", "wrist_cameras",
                  "grasping_mode", "body_geometry_note")
OBJECT_SUMMARY = ("id", "category", "task_names", "fixed_base", "distance_m", "pose",
                  "aabb", "local_states")
CATALOG_FIELDS = ("id", "category", "task_names", "distance_m")
BACKGROUND = {"floors", "ceilings"}
def pick(row, fields):
    return {k: deepcopy(row[k]) for k in fields if k in row}

def specs():
    return [
        {"name": "set_oracle_focus",
         "description": "Select target/destination IDs whose 3D poses should accompany subsequent replies. "
                        "Presentation only: no movement, render or observation revision change. "
                        "Empty IDs clear the focus; only any held object remains selected. Other obstacles remain queryable.",
         "inputSchema": {"type": "object", "properties": {
             "object_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
             "revision": {"type": "integer"}},
             "required": ["object_ids", "revision"], "additionalProperties": False},
         "annotations": {"readOnlyHint": True, "destructiveHint": False}},
        {"name": "get_oracle_geometry",
         "description": "Read FULL geometry of selected objects and/or robot sections from the latest "
                        "cached Oracle snapshot. No new simulator step/render or images. Includes frame, "
                        "units, revision and capture. Use before exact contact, joint or swept-body planning.",
         "inputSchema": {"type": "object", "properties": {
             "object_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
             "object_fields": {"type": "array", "items": {"type": "string", "enum": ["pose", "aabb", "links", "joints", "functional_regions", "local_states", "fixed_base", "distance_m", "task_names"]}, "maxItems": 9},
             "robot_sections": {"type": "array", "items": {"type": "string", "enum": list(ROBOT_SECTIONS)},
                                "maxItems": len(ROBOT_SECTIONS)},
             "revision": {"type": "integer"}},
             "required": ["object_ids", "revision"], "additionalProperties": False},
         "annotations": {"readOnlyHint": True, "destructiveHint": False}},
    ]

class OraclePresentation:
    def __init__(self, *, mode="compact"):
        if mode not in ("compact", "full"):
            raise ValueError("Unknown Oracle presentation")
        self.mode = mode
        self.snapshot, self.focus = None, []

    def remember(self, packet):
        observation = packet.get("observation", {})
        if observation.get("oracle_3d"):
            self.snapshot = deepcopy(observation)

    def _objects(self):
        if self.snapshot is None:
            raise ValueError("Initialize before querying geometry")
        return {o["id"]: o for o in self.snapshot["oracle_3d"]["objects"]}

    def query(self, name, arguments):
        objects = self._objects()
        fields = {"object_ids", "revision"} | ({"robot_sections"} if name == "get_oracle_geometry" else set())
        fields |= {"object_fields"} if name == "get_oracle_geometry" else set()
        if (set(arguments) - fields or not {"object_ids", "revision"} <= set(arguments)
                or type(arguments["revision"]) is not int):
            raise ValueError("Invalid geometry arguments")
        if arguments["revision"] != self.snapshot["revision"]:
            raise ValueError("Stale geometry revision; use the latest tool response")
        ids = arguments["object_ids"]
        if not isinstance(ids, list) or len(ids) > 8 or any(not isinstance(x, str) for x in ids):
            raise ValueError("At most eight object IDs")
        if len(set(ids)) != len(ids) or any(x not in objects for x in ids):
            raise ValueError("Unknown or duplicate object ID; use the current object_catalog")
        if name == "set_oracle_focus":
            self.focus = list(ids)
            return {"ok": True, "presentation_only": True, "revision": self.snapshot["revision"],
                    "focus_ids": list(ids), "targets": [pick(objects[x], ("id", "pose")) for x in ids]}
        if name != "get_oracle_geometry":
            raise ValueError("Unknown presentation tool")
        sections = arguments.get("robot_sections", [])
        object_fields = arguments.get("object_fields", ["pose"])
        allowed = {"pose", "aabb", "links", "joints", "functional_regions", "local_states", "fixed_base", "distance_m", "task_names"}
        if not isinstance(object_fields, list) or len(object_fields)>9 or any(not isinstance(x,str) or x not in allowed for x in object_fields):
            raise ValueError("Select supported object_fields")
        if (not isinstance(sections, list) or len(sections) > len(ROBOT_SECTIONS) or
                any(not isinstance(x, str) or x not in ROBOT_SECTIONS for x in sections) or
                len(set(sections)) != len(sections) or not (ids or sections)):
            raise ValueError("Select objects and/or named robot sections")
        oracle = self.snapshot["oracle_3d"]
        return {"ok": True, "source": "cached_full_oracle_snapshot",
                "revision": self.snapshot["revision"], "capture": deepcopy(self.snapshot.get("capture")),
                "frame": oracle.get("frame"), "units": oracle.get("units"),
                "objects": [pick(objects[x], ("id", *object_fields)) for x in ids],
                "robot": pick(oracle["robot"], sections)}

    def project(self, observation):
        if self.mode == "full" or not observation.get("oracle_3d"):
            return deepcopy(observation)
        result = deepcopy(observation)
        oracle = result["oracle_3d"]
        objects = oracle["objects"]
        robot = oracle["robot"]
        by_id = {o["id"]: o for o in objects}
        held = [a.get("held_object") for a in robot.get("arms", {}).values()]
        selected = [x for x in self.focus if x in by_id]
        selected = list(dict.fromkeys(selected + [x for x in held if isinstance(x, str) and x in by_id]))
        oracle["objects"] = [pick(by_id[x], ("id", "category", "task_names", "pose")) for x in selected]
        oracle["object_catalog"] = [pick(o, ("id", "category", "task_names")) for o in objects]
        oracle["robot"] = {
            "base_pose_world": deepcopy(robot.get("base_pose_world")),
            "arms": {name: pick(arm, ("eef_link", "eef_pose", "held_object"))
                     for name, arm in robot.get("arms", {}).items()},
        }
        oracle["presentation"] = {
            "mode": "compact", "focus_ids": selected,
            "selection": "agent_selected" if self.focus else "none_until_agent_selects",
            "missing_focus_ids": [x for x in self.focus if x not in by_id],
            "not_collision_complete": True,
            "details_tool": "get_oracle_geometry",
            "details": "Query links, joints, functional regions, full finger/body geometry and ready postures "
                       "from this revision before precision/swept-body planning. AABB is only an outer bound.",
            "full_snapshot_recorded_by_backend": True}
        return result

    def packet(self, packet):
        self.remember(packet)
        result = deepcopy(packet)
        if "observation" in result:
            result["observation"] = self.project(result["observation"])
        return result
