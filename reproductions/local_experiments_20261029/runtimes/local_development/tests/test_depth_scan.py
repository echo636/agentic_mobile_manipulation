import math
import unittest

import numpy as np

from manipulation_agent.executors.depth_scan import DepthFrame, ProjectionConfig, project_four_depth_frames, to_mapper_scans
from manipulation_agent.observations.rig import camera_mount


def rig_frame(view, depth, *, pitch=20, origin=(0, 0, 0), capture="capture-1", step=4):
    offset, rotation = camera_mount(view, 1.5, radius=.35, pitch_degrees=pitch)
    transform = np.eye(4)
    transform[:3, :3] = rotation
    transform[:3, 3] = np.asarray(offset) + origin
    h, w = depth.shape
    k = np.array([[w / 2, 0, (w - 1) / 2], [0, w / 2, (h - 1) / 2], [0, 0, 1.]])
    return DepthFrame(view, capture, step, depth, k, transform)


def plane_depth(frame, plane_axis, coordinate):
    h, w = frame.depth_linear.shape
    rows, cols = np.mgrid[:h, :w]
    pixels = np.stack([cols, rows, np.ones_like(cols)], axis=-1)
    rays = pixels @ np.linalg.inv(frame.intrinsic).T * [1, -1, -1]
    rays = rays @ frame.world_from_camera[:3, :3].T
    with np.errstate(divide="ignore", invalid="ignore"):
        result = (coordinate - frame.world_from_camera[plane_axis, 3]) / rays[..., plane_axis]
    return np.where((result > 0) & np.isfinite(result), result, np.nan)


class DepthScanTests(unittest.TestCase):
    def project(self, front, others=None, **config):
        blank = np.full(front.depth_linear.shape, np.nan)
        frames = [front, *(others or [rig_frame(v, blank) for v in ("back", "left", "right")])]
        return project_four_depth_frames(frames, ProjectionConfig(0, pixel_stride=1, **config))

    def test_tilted_camera_floor_is_support_not_obstacle(self):
        template = rig_frame("front", np.ones((33, 33)))
        front = rig_frame("front", plane_depth(template, 2, 0))
        scan = self.project(front)[0]
        self.assertEqual(scan.returns, ())
        self.assertTrue(scan.floor_support)
        self.assertTrue(scan.misses)
        for point in scan.floor_support:
            self.assertAlmostEqual(point.point_world[2], 0, places=10)
        for ray in scan.misses:
            self.assertEqual(ray.camera_origin_world, (.35, 0., 1.5))
            start, end = ray.clear_segment_world
            self.assertLessEqual(start[2], 1.25 + 1e-10)
            self.assertGreaterEqual(end[2], .15 - 1e-10)
            self.assertNotEqual(start, ray.camera_origin_world)

    def test_wall_with_pitch_and_offset_backprojects_to_actual_plane(self):
        template = rig_frame("front", np.ones((33, 33)))
        wall, floor = plane_depth(template, 0, 2.), plane_depth(template, 2, 0.)
        depth = np.fmin(wall, floor)
        scan = self.project(rig_frame("front", depth))[0]
        self.assertTrue(scan.returns)
        for ray in scan.returns:
            self.assertAlmostEqual(ray.measured_surface_world[0], 2., places=10)
            self.assertTrue(.15 <= ray.measured_surface_world[2] <= 1.25)
            self.assertEqual(ray.camera_origin_world[0], .35)
            self.assertLess(ray.clear_segment_world[-1][0], 2.)

    def test_four_origins_and_occlusion_evidence_never_collapse_to_robot_center(self):
        frames = []
        for view in ("front", "back", "left", "right"):
            depth = np.full((3, 3), np.nan)
            # The bottom-center point has height .5 and stays in obstacle band.
            depth[2, 1] = 1.5
            frames.append(rig_frame(view, depth, pitch=0))
        scans = project_four_depth_frames(frames, ProjectionConfig(0, pixel_stride=1))
        self.assertEqual(len({s.camera_origin_world for s in scans}), 4)
        for scan in scans:
            self.assertEqual(len(scan.returns), 1)
            ray = scan.returns[0]
            self.assertEqual(ray.camera_origin_world, scan.camera_origin_world)
            self.assertAlmostEqual(math.hypot(*ray.camera_origin_world[:2]), .35)
            start, end = np.asarray(ray.clear_segment_world)
            origin = np.asarray(ray.camera_origin_world)
            hit = np.asarray(ray.measured_surface_world)
            self.assertLess(np.linalg.norm(np.cross(start-origin, hit-origin)), 1e-9)
            self.assertLess(np.linalg.norm(np.cross(end-origin, hit-origin)), 1e-9)

    def test_invalid_depth_never_creates_misses_or_free_floor(self):
        depth = np.array([[np.nan, np.inf, -np.inf], [0., -1., 100.], [.001, 0., np.nan]])
        scan = self.project(rig_frame("front", depth))[0]
        self.assertEqual(scan.valid_depth_pixels, 0)
        self.assertEqual((scan.returns, scan.misses, scan.floor_support), ((), (), ()))

    def test_nearer_obstacle_suppresses_other_miss_in_same_bin(self):
        depth = np.full((5, 5), np.nan)
        depth[3, 2] = 1.  # z=1.1: obstacle, relative forward=1m
        depth[4, 2] = 1.875  # z=0: floor further along same bearing
        scan = self.project(rig_frame("front", depth, pitch=0))[0]
        self.assertEqual(len(scan.returns), 1)
        self.assertEqual(len(scan.floor_support), 1)
        self.assertEqual(scan.misses, ())
        self.assertEqual(scan.returns[0].pixel, (2, 3))

    def test_finite_surface_beyond_planar_range_is_censored_not_fake_hit(self):
        depth = np.full((33, 33), np.nan)
        depth[17, 16] = 12.
        scan = self.project(rig_frame("front", depth, pitch=0))[0]
        self.assertEqual(scan.returns, ())
        self.assertEqual(len(scan.misses), 1)
        ray = scan.misses[0]
        self.assertTrue(ray.range_censored)
        origin = np.array(ray.camera_origin_world[:2])
        self.assertLessEqual(np.linalg.norm(np.array(ray.clear_segment_world[-1][:2])-origin), 8.0000001)

    def test_frames_are_copied_immutable_and_calibration_is_finite_rigid(self):
        original = np.ones((3, 3))
        frame = rig_frame("front", original)
        original[:] = 99
        self.assertTrue(np.all(frame.depth_linear == 1))
        with self.assertRaises(ValueError):
            frame.depth_linear.setflags(write=True)
        for k in (np.zeros((3, 3)), np.full((3, 3), np.nan)):
            with self.assertRaises(ValueError):
                DepthFrame("front", "c", 0, original, k, np.eye(4))
        transform = frame.world_from_camera.copy()
        transform[0, 0] += .2
        with self.assertRaises(ValueError):
            DepthFrame("front", "c", 0, original, frame.intrinsic, transform)

    def test_capture_must_be_synchronized_and_have_four_distinct_views(self):
        front = rig_frame("front", np.ones((3, 3)))
        with self.assertRaises(ValueError):
            project_four_depth_frames([front] * 4, ProjectionConfig(0))
        others = [rig_frame(v, np.ones((3, 3)), step=5) for v in ("back", "left", "right")]
        with self.assertRaises(ValueError):
            self.project(front, others)

    def test_mapper_packets_transform_actual_origins_and_hits_to_base(self):
        frames = []
        for view in ("front", "back", "left", "right"):
            depth = np.full((3, 3), np.nan)
            depth[2, 1] = 1.5
            frames.append(rig_frame(view, depth, pitch=0, origin=(10, 20, 0)))
        scans = project_four_depth_frames(frames, ProjectionConfig(0, pixel_stride=1))
        packets = to_mapper_scans(scans, (10, 20, math.pi / 2))
        front = packets[0]
        np.testing.assert_allclose(front["origin"], [0, -.35, 0], atol=1e-12)
        np.testing.assert_allclose(front["returns"], [[0, -1.85, 0]], atol=1e-12)
        self.assertEqual(front["misses"], [])
        self.assertEqual(len({tuple(p["origin"]) for p in packets}), 4)
        with self.assertRaises(ValueError):
            to_mapper_scans(scans, (0, 0, float("nan")))

    def test_mapper_floor_misses_remain_misses_and_invalid_depth_stays_absent(self):
        template = rig_frame("front", np.ones((33, 33)))
        scans = self.project(rig_frame("front", plane_depth(template, 2, 0)))
        packets = to_mapper_scans(scans, (0, 0, 0))
        self.assertEqual(packets[0]["returns"], [])
        self.assertTrue(packets[0]["misses"])
        for point in packets[0]["misses"]:
            self.assertLessEqual(math.hypot(point[0] - .35, point[1]), 8.0000001)
        for packet in packets[1:]:
            self.assertEqual((packet["returns"], packet["misses"]), ([], []))

    def test_mapper_censored_obstacle_is_a_clipped_miss_and_occlusion_is_kept(self):
        depth = np.full((33, 33), np.nan)
        depth[17, 16] = 12.
        packet = to_mapper_scans(self.project(rig_frame("front", depth, pitch=0)), (0, 0, 0))[0]
        self.assertEqual(packet["returns"], [])
        np.testing.assert_allclose(packet["misses"], [[8.35, 0, 0]], atol=1e-12)
        depth = np.full((5, 5), np.nan)
        depth[3, 2], depth[4, 2] = 1., 1.875
        packet = to_mapper_scans(self.project(rig_frame("front", depth, pitch=0)), (0, 0, 0))[0]
        self.assertEqual(len(packet["returns"]), 1)
        self.assertEqual(packet["misses"], [])


if __name__ == "__main__":
    unittest.main()
