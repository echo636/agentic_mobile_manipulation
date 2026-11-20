"""rc1 (2026-11-20 22:00, KNOWN_ISSUES #15): refuse to start when the loaded scene does not match construction fields."""
import math

from manipulation_agent.executors.custom_task import construction_mismatch


def yawq(deg):
    h = math.radians(deg) / 2
    return [0., 0., math.sin(h), math.cos(h)]


SPEC_R2 = {'robot_start': {'position': [19.15, 22.6, 0.02], 'orientation': yawq(0.)}}


def test_t7_r2_case_is_refused():
    # T7 r2: robot_start moved to (19.15, 22.6) east, the copied r1 scene loads the robot at (19.15, 18.0) north
    out = construction_mismatch(SPEC_R2, [19.15, 18.0, 0.005], yawq(90.), set())
    assert out['robot_start']['position_error_m'] == 4.6 and out['robot_start']['yaw_error_deg'] == 90.0


def test_matching_start_passes_and_small_drift_tolerated():
    assert construction_mismatch(SPEC_R2, [19.1499, 22.5999, 0.005], yawq(0.01), set()) == {}
    assert construction_mismatch(SPEC_R2, [19.18, 22.63, 0.005], yawq(4.), set()) == {}


def test_yaw_wraparound_and_thresholds():
    spec = {'robot_start': {'position': [0., 0., 0.], 'orientation': yawq(179.)}}
    assert construction_mismatch(spec, [0., 0., 0.], yawq(-179.), set()) == {}
    assert 'robot_start' in construction_mismatch(spec, [0.06, 0., 0.], yawq(179.), set())
    assert 'robot_start' in construction_mismatch(spec, [0., 0., 0.], yawq(172.), set())


def test_excluded_entity_still_present_is_reported():
    spec = {'scene_exclusions': ['garden_chair_cottya_0', 'straight_chair_x']}
    assert construction_mismatch(spec, [0, 0, 0], yawq(0), {'garden_chair_cottya_0', 'table'}) == {
        'scene_exclusions_present': ['garden_chair_cottya_0']}
    assert construction_mismatch({}, [0, 0, 0], yawq(0), {'anything'}) == {}
