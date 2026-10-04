import itertools
import unittest

import numpy as np

from manipulation_agent.executors.depth_scan import DepthFrame, ProjectionConfig, project_four_depth_frames, to_mapper_scans
from manipulation_agent.executors.self_depth_filter import prepare_self_hulls, mask_self_depth


def box(center, half=.1):
    return np.array(list(itertools.product((-half, half), repeat=3))) + center


def frame(depth, view='front', transform=None):
    return DepthFrame(view, 'capture-1', 0, np.array(depth, dtype=float),
                      np.array([[1., 0, 1], [0, 1., 1], [0, 0, 1.]]),
                      np.eye(4) if transform is None else transform)


class SelfDepthFilterTests(unittest.TestCase):
    def test_only_own_body_surface_is_masked_and_raw_depth_is_immutable(self):
        source = frame([[np.nan, np.nan, np.nan], [np.nan, 2., np.nan], [np.nan, np.nan, np.nan]])
        hulls, report = prepare_self_hulls({'body': box([0, 0, -2])})
        filtered, counts = mask_self_depth(source, hulls, pixel_stride=1)
        self.assertTrue(np.isnan(filtered.depth_linear[1, 1]))
        self.assertEqual(source.depth_linear[1, 1], 2.)
        self.assertFalse(filtered.depth_linear.flags.writeable)
        self.assertEqual(counts['self_masked_samples'], 1)
        self.assertEqual(report['hulls_prepared'], 1)
        for depth in (1., 3.):
            other = frame([[np.nan] * 3, [np.nan, depth, np.nan], [np.nan] * 3])
            result, _ = mask_self_depth(other, hulls, pixel_stride=1)
            self.assertEqual(result.depth_linear[1, 1], depth)

    def test_bbox_is_only_preselection_and_does_not_hide_external_points(self):
        tetrahedron = np.array([[0, 0, -3], [1, 0, -3], [0, 1, -3], [0, 0, -2]])
        hulls, _ = prepare_self_hulls({'triangular_link': tetrahedron})
        transform = np.eye(4)
        transform[:3, 3] = [.8, .8, 0]
        source = frame([[np.nan] * 3, [np.nan, 2.8, np.nan], [np.nan] * 3], transform=transform)
        filtered, counts = mask_self_depth(source, hulls, pixel_stride=1)
        self.assertEqual(filtered.depth_linear[1, 1], 2.8)
        self.assertEqual(counts['self_masked_samples'], 0)

    def test_full_camera_world_transform_and_tiny_tolerance_are_applied(self):
        transform = np.eye(4)
        transform[:3, :3] = [[0, 0, -1], [1, 0, 0], [0, -1, 0]]
        transform[:3, 3] = [10, 20, 1.5]
        source = frame([[np.nan] * 3, [np.nan, 2., np.nan], [np.nan] * 3], transform=transform)
        hulls, _ = prepare_self_hulls({'arm': box([12.104, 20, 1.5])})
        filtered, counts = mask_self_depth(source, hulls, pixel_stride=1)
        self.assertEqual(counts['self_masked_samples'], 1)
        self.assertTrue(np.isnan(filtered.depth_linear[1, 1]))
        far_hulls, _ = prepare_self_hulls({'arm': box([12.106, 20, 1.5])})
        result, counts = mask_self_depth(source, far_hulls, pixel_stride=1)
        self.assertEqual(result.depth_linear[1, 1], 2.)

    def test_invalid_depth_and_degenerate_links_do_not_create_rays_or_aabb_masks(self):
        source = frame([[np.inf, np.nan, 0], [-1., 2., np.nan], [np.nan] * 3])
        plane = np.array([[-1, -1, -2], [-1, 1, -2], [1, -1, -2], [1, 1, -2]])
        hulls, report = prepare_self_hulls({'plane': plane, 'missing': None, 'bad': np.full((4, 3), np.nan)})
        self.assertEqual(hulls, ())
        self.assertEqual(len(report['skipped_links']), 3)
        filtered, counts = mask_self_depth(source, hulls, pixel_stride=1)
        np.testing.assert_equal(filtered.depth_linear, source.depth_linear)
        self.assertEqual(counts['self_masked_samples'], 0)

    def test_removed_self_hit_never_turns_into_mapper_free_evidence(self):
        transform = np.eye(4)
        transform[:3, :3] = [[0, 0, -1], [-1, 0, 0], [0, 1, 0]]
        transform[:3, 3] = [0, 0, .8]
        frames = []
        hulls, _ = prepare_self_hulls({'body': box([2, 0, .8])})
        for view in ('front', 'back', 'left', 'right'):
            source = frame([[np.nan] * 3, [np.nan, 2., np.nan], [np.nan] * 3], view=view, transform=transform)
            filtered, _ = mask_self_depth(source, hulls, pixel_stride=1)
            frames.append(filtered)
        scans = project_four_depth_frames(frames, ProjectionConfig(0, pixel_stride=1))
        packets = to_mapper_scans(scans, (0, 0, 0))
        self.assertTrue(all(not p['returns'] and not p['misses'] for p in packets))


if __name__ == '__main__':
    unittest.main()
