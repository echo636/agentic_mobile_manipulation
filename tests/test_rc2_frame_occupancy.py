"""rc2 record layer: private per-frame occupancy and posture (simulator-free)."""
import numpy as np

from manipulation_agent.executors import frame_occupancy as FO


def test_near_geometry_from_depth_only():
    d = np.full((10, 10), 2.0); d[:5] = 0.1
    assert FO.occupancy(d) == {'near_geometry': 0.5}


def test_robot_and_held_fractions_with_pixel_owner():
    d = np.full((4, 4), 1.0)
    ids = np.array([[1, 1, 2, 3]] * 4)
    labels = {'1': '/World/scene_0/robot_r1/head_link/visuals', '2': '/World/scene_0/tray/base_link/visuals', '3': '/World/scene_0/bed/x'}
    out = FO.occupancy(d, ids, labels, '/World/scene_0/robot_r1', ['/World/scene_0/tray'])
    assert out == {'near_geometry': 0.0, 'robot': 0.5, 'held': 0.25}


def test_nothing_held():
    out = FO.occupancy(np.ones((2, 2)), np.zeros((2, 2), int), {'0': '/World/x'}, '/World/scene_0/robot_r1', [])
    assert out['robot'] == 0.0 and out['held'] == 0.0


def test_posture_picks_torso_head():
    class R:
        joints = {'base_footprint_x_joint': 0, 'torso_joint1': 0, 'head_joint': 0, 'left_arm_joint1': 0}
        def get_joint_positions(self): return [0.5, 1.0, -0.2, 0.3]
    assert FO.posture(R()) == {'torso_joint1': 1.0, 'head_joint': -0.2}


def test_private_record_fields_are_private_only():
    """The fields are written to executor_frames/*.json and observation_capture_audit.jsonl only (audience private)."""
    import inspect
    from manipulation_agent.executors import omnigibson_rgb
    src = inspect.getsource(omnigibson_rgb.RGBBackend.observe)
    assert "'occupancy':_occ" in src and "'audience':'executor_private_only'" in src
    assert "'robot_posture':_posture(self.robot)" in src and "'audience':'executor_private'" in src
    ret = src[src.rindex('return '):]
    assert 'occupancy' not in ret and 'posture' not in ret


def test_renderer_pixel_owner_default_on():
    import inspect
    from manipulation_agent.executors import omnigibson_rgb
    src = inspect.getsource(omnigibson_rgb.RGBBackend.__init__)
    assert "os.environ.get('MAS_RENDERER_PIXEL_OWNER','1') == '1'" in src
