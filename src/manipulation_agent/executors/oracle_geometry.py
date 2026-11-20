"""Read-only privileged geometry and contact observations for native operations."""
import math

from ..contracts import SkillError


class OracleGeometry:
    def get_manip_geometry(self, object_ids):
        """Read current physical mesh data; no observation/render/physics work."""
        from .oracle_pick import GeometryPick
        import omnigibson.utils.transform_utils as T
        reference = self.robot.get_position_orientation()
        rotation = T.quat2mat(reference[1]).detach().cpu().numpy()
        origin = reference[0].detach().cpu().numpy()
        objects = []
        for name in object_ids:
            obj = self._resolve_object(name)
            vertices, faces = GeometryPick.mesh(obj)
            objects.append({'id': name, 'collision_mesh': {
                'frame': 'robot_base', 'vertices_m': ((vertices-origin) @ rotation).tolist(),
                'faces': faces.tolist(), 'source': 'native_collision_meshes',
                'note': 'Current physical link geometry, not an AABB or a planned future articulation'}})
        geometry = GeometryPick(self)
        geometry.event = lambda *args, **kwargs: None
        arms = {}
        for arm in self.robot.arm_names:
            offset, aperture = geometry.gripper_geometry(arm)
            arms[arm] = {'grasp_geometry': {'contact_offset_eef_m': offset.tolist(),
                'aperture_m': aperture, 'source': 'native_assisted_grasp_rays_current_opening'}}
        return {'frame': 'robot_base', 'units': {'position': 'metre'}, 'objects': objects,
                'robot': {'arms': arms}}

    def _resolve_object(self, name):
        for obj in self.env.scene.objects:
            if obj.name == name and obj is not self.robot:
                return obj
        raise SkillError("unknown_object", "Object ID is not present in this scene")

    @staticmethod
    def _link_key(obj, link):
        return next((name for name, item in obj.links.items() if item is link),
                    link.prim_path.rsplit("/", 1)[-1])

    def _resolve_link(self, obj, name):
        if name not in obj.links:
            raise SkillError("unknown_link", "Target link is not present on the supplied object")
        link = obj.links[name]
        if getattr(link, "is_meta_link", False):
            raise SkillError("invalid_contact_link", "Use a physical target link; functional regions are separate")
        return link

    def _grasp_state(self, arm):
        obj = (getattr(self.robot, "_ag_obj_in_hand", {}) or {}).get(arm)
        params = (getattr(self.robot, "_ag_obj_constraint_params", {}) or {}).get(arm) or {}
        return {"object_id": None if obj is None else obj.name,
                "target_link": params.get("target_link_name"),
                "constraint_type": params.get("joint_type"),
                "mode": self.oracle_grasping_mode}

    def _contacts(self, arm, obj=None, link=None):
        from omnigibson.utils.usd_utils import RigidContactAPI
        fingers = {finger.prim_path for finger in self.robot.finger_links[arm]}
        targets = ({link.prim_path} if link is not None else
                   {part.prim_path for part in obj.links.values() if not getattr(part, "is_meta_link", False)}
                   if obj is not None else None)
        try:
            pairs = RigidContactAPI.get_contact_pairs(self.env.scene.idx, fingers, targets, current_only=True)
            return {"available": True, "pairs": [list(pair) for pair in sorted(pairs)],
                    "finger_count": len({pair[0] for pair in pairs})}
        except (AssertionError, KeyError, RuntimeError) as exc:
            return {"available": False, "pairs": [], "finger_count": None,
                    "reason": type(exc).__name__}

    def _joint_description(self, obj, name, joint, reference=None):
        import omnigibson.utils.transform_utils as T
        from omnigibson.utils.constants import JointType
        kind = {JointType.JOINT_PRISMATIC: "prismatic",
                JointType.JOINT_REVOLUTE: "revolute",
                JointType.JOINT_FIXED: "fixed",
                JointType.JOINT_SPHERICAL: "spherical"}.get(joint.joint_type, "unknown")
        row = {"id": name, "type": kind, "parent_link": str(joint.body0).rsplit("/", 1)[-1],
               "child_link": str(joint.body1).rsplit("/", 1)[-1]}
        if kind not in {"prismatic", "revolute"} or not joint.articulated:
            return row
        parent = obj.links[row["parent_link"]]
        parent_pose = parent.get_position_orientation()
        local_position = joint.local_position_0 * parent.scale
        origin, orientation = T.pose_transform(*parent_pose, local_position, joint.local_orientation_0)
        reference = reference or self.robot.get_position_orientation()
        origin_base, orientation_base = T.relative_pose_transform(origin, orientation, *reference)
        axis = self.torch.zeros(3, device=origin_base.device)
        axis["XYZ".index(joint.axis)] = 1.
        direction = T.quat2mat(orientation_base) @ axis
        position, velocity, effort = joint.get_state()
        scalar = lambda value: float(value.flatten()[0])
        lower, upper = float(joint.lower_limit), float(joint.upper_limit)
        has_limit = bool(getattr(joint, 'has_limit', True))
        row.update(position=scalar(position), velocity=scalar(velocity), effort=scalar(effort),
                   lower_limit=lower if has_limit and math.isfinite(lower) else None,
                   upper_limit=upper if has_limit and math.isfinite(upper) else None,
                   units="metre" if kind == "prismatic" else "radian",
                   axis_origin_m=self._list(origin_base), axis_direction=self._list(direction),
                   frame="robot_base")
        return row

    def _joint_rows(self, obj, reference):
        rows = []
        for name, joint in list(obj.joints.items())[:48]:
            try:
                rows.append(self._joint_description(obj, name, joint, reference))
            except (AssertionError, KeyError, RuntimeError, ValueError, AttributeError) as exc:
                rows.append({"id": name, "available": False, "reason": type(exc).__name__})
        return rows

    def _functional_regions(self, obj, reference):
        rows = []
        for name, link in obj.links.items():
            if getattr(link, "is_meta_link", False):
                rows.append({"id": name, "kind": "authored_meta_region",
                             "pose": self._pose(self._relative(link.get_position_orientation(), reference)),
                             "aabb": self._box(link, reference), "surface_normal": None})
        from omnigibson.object_states import ToggledOn
        state = obj.states.get(ToggledOn)
        if state is not None:
            link = state.link
            marker_pose = state.visual_marker.get_position_orientation()
            marker_relative = self._relative(marker_pose, reference)
            center = self._list(marker_relative[0])
            radius = float(self.torch.min(state.visual_marker.extent * state.scale * link.scale))
            # Match the pinned native overlap_sphere, not a zero-size meta-link AABB.
            activation_box = {"min_m": [value-radius for value in center],
                              "max_m": [value+radius for value in center],
                              "center_m": center, "size_m": [2*radius]*3}
            row = {"id": link.name, "kind": "togglebutton",
                   "pose": self._pose(marker_relative), "aabb": activation_box,
                   "activation_sphere": {"center_m": center, "radius_m": radius,
                                         "source": "native_ToggledOn_overlap_sphere"},
                   "surface_normal": None, "eligible_contact": "robot finger links",
                   "activation": "native finger contact and region overlap for configured consecutive state updates",
                   "requires_closed": bool(state.requires_closed)}
            rows = [item for item in rows if item["id"] != link.name] + [row]
        return rows[:32]

    def _local_states(self, obj):
        from omnigibson.object_states import Open, ToggledOn
        return {label: bool(obj.states[cls].get_value()) for label, cls in
                (("open", Open), ("toggled_on", ToggledOn)) if cls in obj.states}

    def _oracle_object_details(self, obj, reference):
        links = []
        for name, link in list(obj.links.items())[:64]:
            if not getattr(link, "is_meta_link", False):
                links.append({"name": name,
                              "pose": self._pose(self._relative(link.get_position_orientation(), reference)),
                              "aabb": self._box(link, reference)})
        return {"links": links, "joints": self._joint_rows(obj, reference),
                "functional_regions": self._functional_regions(obj, reference),
                "local_states": self._local_states(obj),
                "geometry_note": "AABBs are outer bounds; authored meta regions are not automatically certified openings."}

    def _is_articulated_handle(self, obj, link):
        import networkx as nx
        from omnigibson.utils.constants import JointType
        name = self._link_key(obj, link)
        graph = obj.articulation_tree
        if name not in graph:
            return False
        return any(graph.edges[edge[:2]]["joint_type"] in {
            JointType.JOINT_PRISMATIC, JointType.JOINT_REVOLUTE}
            for edge in nx.edge_dfs(graph, name, orientation="reverse"))

    def _retention(self, arm, obj, link):
        native = self._grasp_state(arm)
        contacts = self._contacts(arm, obj, link)
        assisted = (native["object_id"] == obj.name and native["target_link"] == self._link_key(obj, link))
        physical = self.oracle_grasping_mode == "physical" and contacts["available"] and contacts["finger_count"] >= 2
        return {"retained": bool(assisted or physical), "native_grasp": native, "contacts": contacts,
                "evidence": "native_assisted_link" if assisted else
                            "two_finger_contact_candidate" if physical else "no_retention_evidence"}

    def _native_toggle(self, obj):
        from omnigibson.object_states import Open, ToggledOn
        state = obj.states.get(ToggledOn)
        requires_closed_and_open = (state is not None and state.requires_closed and
                                   Open in obj.states and obj.states[Open].get_value())
        return state, bool(requires_closed_and_open)

    def _relation(self, obj, destination, relation):
        from omnigibson.object_states import Inside, OnTop
        cls = Inside if relation == "inside" else OnTop
        if cls not in obj.states:
            return None
        return bool(obj.states[cls].get_value(destination))
