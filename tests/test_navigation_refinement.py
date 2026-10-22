"""World coordinates and floor limits for action-local map refinement."""
import unittest

import numpy as np

from manipulation_agent.executors.gt_navigation import GridMap
from manipulation_agent.executors.live_gt_map import floor_support_window


class FloorWindowTests(unittest.TestCase):
    def test_refinement_preserves_world_alignment_and_unsupported_floor(self):
        raw = np.ones((12, 18), dtype='uint8')
        raw[:, 7:9] = 0
        raw[3:5, 12:] = 0
        coarse = GridMap(18, 12, .05, (-18*.05/2, -12*.05/2), raw.tobytes())
        fine, origin = floor_support_window(raw, .05, .025, [-.6, -.35, .6, .4])
        grid = GridMap(fine.shape[1], fine.shape[0], .025, tuple(origin), fine.tobytes())
        for row in range(grid.height):
            for col in range(grid.width):
                xy = grid.world((row, col))
                self.assertEqual(grid.navigable((row,col)), coarse.navigable(coarse.cell(xy)))
        self.assertFalse(grid.navigable(grid.cell((-.05, 0.))))
        self.assertFalse(grid.navigable(grid.cell((.5, 0.))))
        self.assertTrue(grid.navigable(grid.cell((.15, 0.))))

    def test_finer_resolution_requires_a_bounded_finite_window(self):
        raw = np.ones((10,10), dtype='uint8')
        for window in (None, [0.,0.,100.,100.], [0.,0.,float('inf'),1.], [1.,0.,0.,1.]):
            with self.subTest(window=window), self.assertRaises(ValueError):
                floor_support_window(raw,.05,.025,window)


if __name__ == '__main__':
    unittest.main()
