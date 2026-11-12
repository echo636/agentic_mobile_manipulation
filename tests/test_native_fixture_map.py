import unittest
from types import SimpleNamespace

import numpy as np


class _Map:
    """Minimal stand-in for OmniGibson's TraversableMap index conventions."""
    def __init__(self):
        self.map_resolution = .1
        self.floor_heights = [0.]
        self.floor_map = [np.full((40, 40), 255, dtype='uint8')]

    def world_to_map(self, xy):
        return np.floor(np.asarray(xy, dtype=float)[::-1]/self.map_resolution + 20).astype(int)


def _backend(objects, names):
    registry = lambda key, name, default=None: objects.get(name, default)
    return SimpleNamespace(env=SimpleNamespace(scene=SimpleNamespace(trav_map=_Map(), object_registry=registry)),
                           _native_map_added_fixtures=names)


class AddedFixtureMapTests(unittest.TestCase):
    def test_declared_fixture_footprint_is_blocked_and_reapplied(self):
        from manipulation_agent.executors.compat.native_astar import sync_added_fixture_map
        table = SimpleNamespace(aabb=(np.array([.0, .5, 0.]), np.array([1., 1., .65])))
        backend = _backend({'stage': table}, ['stage', 'missing'])
        self.assertEqual(sync_added_fixture_map(backend), ['stage'])
        floor = backend.env.scene.trav_map.floor_map[0]
        rows, cols = np.nonzero(floor == 0)
        # world x -> columns 20..30, world y -> rows 25..30
        self.assertEqual((rows.min(), rows.max(), cols.min(), cols.max()), (25, 30, 20, 30))
        # A later map rewrite (door sync) is repaired by the next call.
        backend.env.scene.trav_map.floor_map[0] = np.full((40, 40), 255, dtype='uint8')
        sync_added_fixture_map(backend)
        self.assertEqual(int((backend.env.scene.trav_map.floor_map[0] == 0).sum()), 6*11)

    def test_flat_or_overhead_supports_and_undeclared_tasks_leave_the_map(self):
        from manipulation_agent.executors.compat.native_astar import sync_added_fixture_map
        mat = SimpleNamespace(aabb=(np.array([0., 0., 0.]), np.array([1., 1., .02])))
        shelf = SimpleNamespace(aabb=(np.array([0., 0., 1.8]), np.array([1., 1., 2.])))
        backend = _backend({'mat': mat, 'shelf': shelf}, ['mat', 'shelf'])
        self.assertEqual(sync_added_fixture_map(backend), [])
        self.assertTrue((backend.env.scene.trav_map.floor_map[0] == 255).all())
        self.assertEqual(sync_added_fixture_map(_backend({}, [])), [])


if __name__ == '__main__':
    unittest.main()
