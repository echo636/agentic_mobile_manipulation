import numpy as np
import pytest

from manipulation_agent.trajectory_timing import compact_path, dense_joint_samples


def curve(steps):
    t = np.linspace(0, 1, steps+1)
    return (1.2*(10*t**3-15*t**4+6*t**5))[:, None]


def test_825_step_path_can_finish_in_100_without_losing_its_endpoint():
    raw = curve(825)
    q, timing = compact_path(raw, 1/30, [1.], [3.], [30.])
    assert len(q) == 101
    assert np.allclose(q[[0, -1]], raw[[0, -1]])
    assert timing['source_control_steps'] == 825
    assert timing['control_steps'] == 100 and timing['within_joint_limits']


def test_short_path_is_not_stretched_and_stationary_goal_is_supported():
    raw = curve(62)*.1
    q, timing = compact_path(raw, 1/30, [1.], [3.], [30.])
    assert np.array_equal(q, raw) and timing['control_steps'] == 62
    q, timing = compact_path([[.3]], 1/30, [1.], [3.], [30.])
    assert np.array_equal(q, [[.3], [.3]])


def test_rate_violation_returns_no_executable_path_instead_of_truncation():
    q, info = compact_path(curve(825), 1/30, [.01], [.01], [.01])
    assert q is None and not info['within_joint_limits']
    assert max(info['limit_ratios'].values()) > 1


def test_dense_collision_samples_cover_skipped_command_interpolants():
    q = dense_joint_samples([[0., 0.], [.12, -.01]], max_joint_increment=.02)
    assert np.max(np.abs(np.diff(q, axis=0))) <= .02+1e-9
    assert np.allclose(q[[0, -1]], [[0., 0.], [.12, -.01]])


@pytest.mark.parametrize('limits', [([0.], [1.], [1.]), ([np.nan], [1.], [1.])])
def test_invalid_limits_fail_before_output(limits):
    with pytest.raises(ValueError):
        compact_path(curve(10), 1/30, *limits)
