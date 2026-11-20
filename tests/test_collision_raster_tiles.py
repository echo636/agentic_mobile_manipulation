"""Enclosing queries must preserve thin barriers and partial dirty updates."""
from types import SimpleNamespace
import unittest
import numpy as np
from manipulation_agent.executors.live_gt_map import rasterize_collision_cells


class CollisionRasterTiles(unittest.TestCase):
    def test_sparse_barriers_match_exhaustive_columns(self):
        shape = (65, 67)
        resolution = .05
        origin = np.array([-1.37, -.91])
        boxes = [('wall', np.array([-.121, -.6, .1]), np.array([-.119, 1.5, .8])),
                 ('prop', np.array([.7, .8, .2]), np.array([.85, .9, .3])),
                 ('ignored', np.array([-10., -10., -10.]), np.array([10., 10., 10.]))]

        def overlap(half, center, rotation, callback, any_hit):
            low, high = np.asarray(center)-half, np.asarray(center)+half
            for name, lo, hi in reversed(boxes):
                if np.all(high >= lo) and np.all(low <= hi):
                    if callback(SimpleNamespace(rigid_body=name)) is False:
                        break

        rr, cc = np.indices(shape)
        centers = np.stack((origin[0]+cc*resolution, origin[1]+rr*resolution,
                            np.full(shape, .5)), axis=-1)
        half = np.array([resolution/2, resolution/2, .5])
        expected = np.ones(shape, dtype='uint8')
        for name, lo, hi in boxes[:2]:
            expected[np.all(centers+half >= lo, axis=-1) &
                     np.all(centers-half <= hi, axis=-1)] = 0
        for partial in (False, True):
            with self.subTest(partial=partial):
                dirty = (rr+cc) % 3 == 0 if partial else np.ones(shape, dtype=bool)
                raw = np.full(shape, 9, dtype='uint8')
                queries = rasterize_collision_cells(SimpleNamespace(overlap_box=overlap),
                    raw, dirty, origin, resolution, 0., 1., {'ignored'}, lambda: None)
                np.testing.assert_array_equal(raw[dirty], expected[dirty])
                self.assertTrue(np.all(raw[~dirty] == 9))
                self.assertLess(queries, np.count_nonzero(dirty)//2)

    def test_empty_region_collapses_and_cancellation_is_preserved(self):
        query = SimpleNamespace(overlap_box=lambda *args: None)
        raw = np.zeros((64, 64), dtype='uint8')
        dirty = np.ones_like(raw, dtype=bool)
        count = rasterize_collision_cells(query, raw, dirty, (0., 0.), .05,
                                          0., 1., set(), lambda: None)
        self.assertEqual(count, 4)
        self.assertTrue(np.all(raw == 1))
        def cancelled():
            raise RuntimeError('cancelled')
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            rasterize_collision_cells(query, raw, dirty, (0., 0.), .05,
                                      0., 1., set(), cancelled)
