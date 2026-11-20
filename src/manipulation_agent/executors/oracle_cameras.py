"""Stock R1Pro wrist RGB integration; simulator imports remain inside the adapter."""
from ..contracts import SkillError

WRIST_VIEWS = ("left_wrist", "right_wrist")


def select_wrist_sensors(sensors):
    """Use the asset-authored wrist cameras; reject unexpected/ambiguous cameras."""
    result = {}
    for name, sensor in sensors.items():
        if ":Camera:" not in name:
            continue
        matches = [arm + "_wrist" for arm in ("left", "right")
                   if f":{arm}_realsense_link:Camera:" in name]
        if len(matches) != 1 or matches[0] in result:
            raise RuntimeError("Unexpected or duplicate stock camera: " + name)
        result[matches[0]] = sensor
    if set(result) != set(WRIST_VIEWS):
        raise RuntimeError("Both asset-authored R1Pro wrist cameras are required")
    return {view: result[view] for view in WRIST_VIEWS}


class OracleWristCameras:
    def _additional_rgb_sensors(self):
        if not getattr(self, "wrist_cameras_enabled", False):
            return super()._additional_rgb_sensors()
        sensors = select_wrist_sensors(self.robot.sensors)
        with self._startup_stage("stock_wrist_camera_calibration"):
            for sensor in sensors.values():
                sensor.initialize_sensors(names="camera_params")
            # Sensors are already attached to the original physical wrist links.
            # Do not reposition them, rotate the robot, or advance physics.
            for _ in range(20):
                self.og.sim.render()
        return sensors

    def _wrist_camera_contract(self):
        return {
            "enabled": bool(getattr(self, "wrist_cameras_enabled", False)),
            "views": list(getattr(self, "extra_rgb_sensors", {})),
            "mount": "asset-authored R1Pro left/right_realsense_link/Camera",
            "follows": "corresponding wrist link, including arm and torso motion",
            "capture": "same frozen simulation state and capture_id as surround RGB",
            "purpose": "manipulation visual feedback; navigate_to uses surround RGB only",
            "axes": "USD camera: +x image right, +y image up, -z optical forward",
            "model_input": "RGB plus explicit Oracle camera calibration; no depth image",
        }

    def _wrist_camera_metadata(self, images):
        refs = {frame["view"]: frame["image_ref"] for frame in images}
        result = {}
        for view, sensor in getattr(self, "extra_rgb_sensors", {}).items():
            arm = view.removesuffix("_wrist")
            pose = sensor.get_position_orientation()
            result[view] = {
                "arm": arm, "image_ref": refs[view],
                "mount_link": arm + "_realsense_link",
                "pose_in_robot_base": self._pose(self._relative(pose)),
                "pose_in_eef": self._pose(self._relative(
                    pose, self.robot.eef_links[arm].get_position_orientation())),
                "intrinsic_matrix_px": self._list(self._sensor_intrinsics[view]),
                "width": self.image_size, "height": self.image_size,
                "axes": "USD: +x right, +y up, -z optical forward",
            }
        return result

    def _ground(self, target, **kwargs):
        frame = self.current_frames.get(target["image_ref"], {})
        if frame.get("view") in WRIST_VIEWS:
            raise SkillError("invalid_navigation_view",
                             "Use current front/back/left/right RGB for navigate_to; "
                             "wrist cameras are for manipulation feedback")
        return super()._ground(target, **kwargs)
