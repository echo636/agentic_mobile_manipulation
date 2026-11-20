"""Native process integration tests; opt in with MAS_CARTOGRAPHER_RUNTIME.

These exercise the actual compiled Cartographer library, not a mock map.
"""
import hashlib
import math
import os
from pathlib import Path
import tempfile
import time
import unittest

from manipulation_agent.executors.cartographer_mapping import CartographerMapper


def scan(*, origin=(.025, .025, 0), hits=(), misses=()):
    return {'view': 'front', 'origin': origin, 'returns': hits, 'misses': misses}


def value(snapshot, x, y):
    col = math.floor((x - snapshot.origin[0]) / snapshot.resolution + .5)
    row = math.floor((y - snapshot.origin[1]) / snapshot.resolution + .5)
    if not 0 <= col < snapshot.width or not 0 <= row < snapshot.height:
        return 255
    return snapshot.occupancy[row * snapshot.width + col]


@unittest.skipUnless(os.environ.get('MAS_CARTOGRAPHER_RUNTIME'), 'requires native project runtime')
class NativeCartographerTests(unittest.TestCase):
    def setUp(self):
        persistent = os.environ.get('MAS_MAPPER_TEST_OUTPUT')
        if persistent:
            self.output = Path(persistent) / self._testMethodName
        else:
            directory = tempfile.TemporaryDirectory()
            self.addCleanup(directory.cleanup)
            self.output = Path(directory.name)
        from manipulation_agent.deadline import EpisodeDeadline
        self.mapper = CartographerMapper(self.output, deadline=EpisodeDeadline(time.time() + 30))
        self.addCleanup(self.mapper.close)

    def update(self, scans=(), pose=(0, 0, 0), timestamp=0):
        return self.mapper.update(scans, pose=pose, timestamp=timestamp)

    def test_empty_and_invalid_input_never_create_free_space(self):
        snapshot = self.update()
        self.assertEqual(set(snapshot.occupancy), {255})
        self.assertEqual((snapshot.width, snapshot.height), (64, 64))
        with self.assertRaises(ValueError):
            self.update([scan(hits=[(float('nan'), 0, 0)])])
        unchanged = self.update()
        self.assertEqual(snapshot.occupancy, unchanged.occupancy)

    def test_offset_camera_origin_does_not_clear_base_to_camera(self):
        snapshot = self.update([scan(origin=(1.025, .025, 0), hits=[(3.025, .025, 0)])])
        self.assertEqual(value(snapshot, 0, .025), 255)
        self.assertEqual(value(snapshot, 2.025, .025), 0)
        self.assertEqual(value(snapshot, 3.025, .025), 100)
        self.assertEqual(value(snapshot, 2.025, .525), 255)

    def test_world_orientation_and_floor_misses(self):
        snapshot = self.update([scan(hits=[(2.025, .025, 0)], misses=[(.025, -2.025, 0)])], pose=(10, 20, math.pi / 2))
        self.assertEqual(value(snapshot, 9.975, 22.025), 100)
        self.assertEqual(value(snapshot, 9.975, 21.025), 0)
        self.assertEqual(value(snapshot, 11.025, 20.025), 0)
        self.assertNotEqual(value(snapshot, 12.025, 20.025), 100)

    def test_dynamic_obstacle_blocks_immediately_after_saturated_free(self):
        clear = scan(misses=[(3.025, .025, 0)])
        for _ in range(65):
            old = self.update([clear])  # duplicate sim timestamps are valid
        self.assertEqual(value(old, 2.025, .025), 0)
        old_hash = hashlib.sha256(Path(old.metadata['file']).read_bytes()).hexdigest()
        snapshot = self.update([clear, scan(hits=[(2.025, .025, 0)])])
        self.assertEqual(value(snapshot, 2.025, .025), 100)
        snapshot = self.update()  # unobserved now must retain the obstacle
        self.assertEqual(value(snapshot, 2.025, .025), 100)
        for _ in range(2):
            snapshot = self.update([clear])
            self.assertEqual(value(snapshot, 2.025, .025), 100)
        snapshot = self.update([clear])
        self.assertEqual(value(snapshot, 2.025, .025), 0)
        self.assertEqual(old_hash, hashlib.sha256(Path(old.metadata['file']).read_bytes()).hexdigest())

    def test_shared_episode_cancel_stops_update_without_child_leak(self):
        from manipulation_agent.deadline import EpisodeDeadline
        from manipulation_agent.contracts import SkillError
        deadline = EpisodeDeadline()
        self.mapper.deadline = deadline
        deadline.request_stop()
        with self.assertRaises(SkillError):
            self.update([scan(hits=[(2, 0, 0)])])
        self.mapper.close()
        self.assertIsNotNone(self.mapper._process.poll())

    def test_removed_saturated_obstacle_clears_only_after_valid_evidence(self):
        for _ in range(20):
            self.update([scan(hits=[(2.025, .025, 0)])])
        for _ in range(4):
            snapshot = self.update()
            self.assertEqual(value(snapshot, 2.025, .025), 100)
        for _ in range(2):
            snapshot = self.update([scan(misses=[(3.025, .025, 0)])])
            self.assertEqual(value(snapshot, 2.025, .025), 100)
        snapshot = self.update([scan(misses=[(3.025, .025, 0)])])
        self.assertEqual(value(snapshot, 2.025, .025), 0)

    def test_exact_positive_and_negative_boundaries_match_native_hit_cells(self):
        # Compare native initially unknown hits to identical hits after saturated
        # free history; the hit overlay must select the same native cells.
        hits = [[2., 0., 0.], [-2., 0., 0.], [0., 2., 0.], [0., -2., 0.]]
        fresh_dir = self.output / 'fresh_reference'
        with CartographerMapper(fresh_dir) as fresh:
            reference = fresh.update([scan(origin=(0, 0, 0), hits=hits)], pose=(0, 0, 0), timestamp=0)
        for _ in range(65):
            self.update([scan(origin=(0, 0, 0), misses=[[3, 0, 0], [-3, 0, 0], [0, 3, 0], [0, -3, 0]])])
        result = self.update([scan(origin=(0, 0, 0), hits=hits)])
        checked = 0
        for row in range(reference.height):
            for col in range(reference.width):
                if reference.occupancy[row*reference.width+col] == 100:
                    x = reference.origin[0]+col*reference.resolution
                    y = reference.origin[1]+row*reference.resolution
                    self.assertEqual(value(result, x, y), 100)
                    checked += 1
        self.assertEqual(checked, 4)

    def test_dead_worker_is_reaped_and_close_records_failure(self):
        self.mapper._process.kill()
        self.mapper._process.wait(timeout=5)
        with self.assertRaises((RuntimeError, BrokenPipeError)):
            self.update()
        self.mapper.close()
        import json
        record = json.loads((self.output / 'worker.json').read_text())
        self.assertEqual(record['status'], 'failed')

    def test_large_packet_crosses_pipe_capacity_without_truncation(self):
        snapshot = self.update([scan(misses=[(3.025, .025, 0)] * 4096)])
        self.assertEqual(snapshot.metadata['misses'], 4096)
        self.assertEqual(value(snapshot, 2.025, .025), 0)

    def test_crop_expansion_preserves_world_cell_centres_in_double_precision(self):
        pose = (4.901851654052734, 3.8323161602020264, 0.)
        initial = self.update([scan(misses=[(3.025, .025, 0)])], pose=pose)
        fixed = (initial.origin[0] + 20*initial.resolution, initial.origin[1])
        for step in range(1, 9):
            snapshot = self.update([scan(origin=(-step*.25, -step*.1, 0),
                misses=[(-2-step*.25, -1-step*.1, 0)])], pose=pose)
            col = round((fixed[0]-snapshot.origin[0])/snapshot.resolution)
            row = round((fixed[1]-snapshot.origin[1])/snapshot.resolution)
            current = (snapshot.origin[0]+col*snapshot.resolution,
                       snapshot.origin[1]+row*snapshot.resolution)
            self.assertLess(math.dist(fixed, current), 1e-10)
            self.assertEqual(value(snapshot, *fixed), 0)


if __name__ == '__main__':
    unittest.main()
