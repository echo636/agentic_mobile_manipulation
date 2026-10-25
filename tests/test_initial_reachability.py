import numpy as np

from manipulation_agent.executors.initial_reachability import metric_reachability_maps


def test_wall_reach_matches_metres_and_standing_clearance():
    floor = np.zeros((101, 101), dtype=np.uint8)
    floor[:, :51] = 255
    standing, reachable = metric_reachability_maps(floor, .05, .4, .8)
    assert standing[50, 42] == 255
    assert standing[50, 43] == 0
    # A wall at column 50: arm reach extends 16 cells, not eight.
    assert reachable[50, 66] == 255
    assert reachable[50, 67] == 0


def test_metric_windows_are_symmetric_and_do_not_open_narrow_corridor():
    floor = np.zeros((101, 101), dtype=np.uint8)
    floor[30:71, 30:71] = 255
    standing, reachable = metric_reachability_maps(floor, .05, .4, .8)
    for result in (standing, reachable):
        np.testing.assert_array_equal(result, result[::-1, ::-1])
    floor[:] = 0
    floor[:, 47:54] = 255  # 35 cm corridor cannot fit the 80 cm map footprint.
    standing, reachable = metric_reachability_maps(floor, .05, .4, .8)
    assert not standing.any()
    assert not reachable.any()
