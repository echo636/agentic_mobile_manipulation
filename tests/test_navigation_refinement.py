"""World coordinates and floor limits for action-local map refinement."""
import unittest
import importlib.util

import numpy as np

from manipulation_agent.executors.navigation.gt_navigation import GridMap
from manipulation_agent.executors.navigation.live_gt_map import floor_support_window, repair_floor_support


@unittest.skipUnless(importlib.util.find_spec('cv2'), 'navigation geometry dependency')
class ApproachWindowTests(unittest.TestCase):
    def test_projected_floor_route_rechecks_original_target_on_finer_geometry(self):
        from dataclasses import replace
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from types import SimpleNamespace
        from unittest.mock import patch
        from manipulation_agent.executors.navigation.gt_navigation import NavigationPlan
        from manipulation_agent.executors.navigation.heading_collision import HeadingCollisionChecker
        from manipulation_agent.executors.navigation.forward_navigation import route_from_poses
        from manipulation_agent.executors.navigation.navigation_refinement import refine_navigation_detour
        support = np.ones((80, 80), dtype='uint8')
        raw = support.copy()
        raw[:, 40] = 0
        grid = GridMap(80, 80, .05, (-2., -2.), support.tobytes())
        footprint = np.array([[-.1,-.1], [.1,-.1], [.1,.1], [-.1,.1]])
        collision = HeadingCollisionChecker(grid, [(raw, footprint)], 0., support)
        actual = (-1., 0., 0.)
        short = NavigationPlan((actual[:2], (-.5, 0.)), (-.5, 0.), (1., 0.),
                               0., .5, 1.5, 1, 1, 0., 0, 0.)
        exact = replace(short, points=(actual[:2], (1.,0.)), goal=(1.,0.),
                        geodesic_m=2., score=0.)
        route = route_from_poses(short, [actual, (-.5,0.,0.)], collision)
        with TemporaryDirectory() as output:
            for opening in (False, True):
                with self.subTest(opening=opening):
                    backend = SimpleNamespace(output=Path(output), robot=SimpleNamespace(is_holonomic_base=True),
                        deadline=SimpleNamespace(check=lambda:None), _navigation_layers=collision.layers,
                        _navigation_support=support, _navigation_clearance=None, _navigation_layer_yaw=0.)
                    def build(backend, floor, sampling):
                        resolution = sampling['resolution']
                        size = round(4/resolution)
                        fine_support = np.ones((size,size), dtype='uint8')
                        fine_raw = fine_support.copy()
                        fine_raw[:,size//2] = 0
                        if opening:
                            fine_raw[round(1.6/resolution):round(2.4/resolution),size//2] = 1
                        finer = GridMap(size,size,resolution,(-2.,-2.),fine_support.tobytes())
                        backend._navigation_layers = [(fine_raw,footprint)]
                        backend._navigation_support = fine_support
                        return finer, {'query_seconds':0.}
                    with patch('manipulation_agent.executors.navigation.live_gt_map.build_navigation_grid', side_effect=build):
                        result = refine_navigation_detour(backend,0,grid,{},actual,
                            [short,exact],short,route,collision,{},project_point=True)
                    if opening:
                        self.assertEqual(result[2].goal, exact.goal)
                        self.assertGreater(result[3].distance_m, route.distance_m)
                        self.assertEqual(result[6]['status'], 'refined_route_selected')
                    else:
                        self.assertIs(result[2], short)
                        self.assertIs(result[3], route)
                        self.assertIs(backend._navigation_layers, collision.layers)

    def test_refinement_keeps_detour_around_long_obstacle_and_body_halo(self):
        from manipulation_agent.executors.navigation.heading_collision import HeadingCollisionChecker
        from manipulation_agent.executors.navigation.navigation_refinement import approach_sampling
        support = np.ones((100, 100), dtype='uint8')
        raw = support.copy()
        raw[:75, 47:54] = 0
        grid = GridMap(100, 100, .1, (-5., -4.), support.tobytes())
        footprint = np.array([[-.2,-.2], [.2,-.2], [.2,.2], [-.2,.2]])
        check = lambda: None
        collision = HeadingCollisionChecker(grid, [(raw, footprint)], 0., support, check_cancelled=check)
        sampling = approach_sampling(collision, (-1.,0.,0.), (1.,0.), 1.4, check)
        window = sampling['window']
        self.assertGreaterEqual(sampling['resolution'], .0125)
        self.assertLess(sampling['resolution'], .025)
        sampled, _ = floor_support_window(support, .1, sampling['resolution'], window)
        self.assertLessEqual(sampled.size, 250000)
        # The old y<=2.1 crop removes the only route, even after resampling.
        # Query space must cover the detour's body, not just its base centre.
        self.assertGreater(window[3], 4.)
        self.assertLessEqual(window[0], -3.1 + 1e-9)
        self.assertGreaterEqual(window[2], 3.1 - 1e-9)
        self.assertIs(collision.check_cancelled, check)
        self.assertTrue(np.array_equal(collision.layers[0][0], raw))


class FloorWindowTests(unittest.TestCase):
    def test_approach_resolution_includes_fractional_alignment_at_cell_limit(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from manipulation_agent.executors.navigation.navigation_refinement import approach_sampling
        raw = np.ones((1000, 1000), dtype='uint8')
        collision = SimpleNamespace(grid=SimpleNamespace(resolution=.05))
        # R999/R1000 failed before search: the continuous area fit omitted
        # one fractional alignment cell on each axis of rectangular windows.
        for width, height, offset in [(13, 9, 0), (40, 24, 0), (11, 13, .02)]:
            window = [offset, offset, width+offset, height+offset]
            with patch('manipulation_agent.executors.navigation.navigation_refinement.approach_refinement_window', return_value=window):
                sampling = approach_sampling(collision, (0, 0, 0), (1, 1), 1.4, lambda: None)
            sampled, origin = floor_support_window(raw, .05, sampling['resolution'], window)
            self.assertLessEqual(sampled.size, 250000)
            self.assertTrue(np.all(origin <= window[:2]))
            self.assertTrue(np.all(origin + (np.array(sampled.shape[::-1])-1)*sampling['resolution'] >= window[2:]))

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


class LiveFloorSupportTests(unittest.TestCase):
    def test_recovers_a_doorway_omitted_by_the_traversal_image(self):
        from types import SimpleNamespace
        raw = np.ones((7, 9), dtype='uint8')
        raw[3, :] = 0
        def ray(origin, direction, distance, callback):
            # Structural floor ends at x=.6; the remaining black pixels are
            # a genuine void, even though their neighbours have floor.
            x, y, _ = origin
            callback(SimpleNamespace(rigid_body='robot', normal=(0.,0.,1.), position=(x,y,.01)))
            if x < .6:
                callback(SimpleNamespace(rigid_body='floor', normal=(0.,0.,1.), position=(x,y,0.)))
        repaired, details = repair_floor_support(raw,(0.,0.),.1,0.,{'floor'},
            SimpleNamespace(raycast_all=ray),lambda:None)
        self.assertTrue(repaired[3,3])
        self.assertFalse(repaired[3,7])
        self.assertFalse(raw[3,3])
        self.assertGreater(details['added_cells'],0)
        # White traversal pixels must not retain support over a real void.
        self.assertTrue(raw[2,7])
        self.assertFalse(repaired[2,7])
        self.assertGreater(details['removed_cells'],0)

    def test_rejects_a_cell_with_an_unsupported_corner(self):
        from types import SimpleNamespace
        def ray(origin, direction, distance, callback):
            x,y,_=origin
            if x > 0 and y > 0:return
            callback(SimpleNamespace(rigid_body='floor',normal=(0.,0.,1.),position=(x,y,0.)))
        repaired,_=repair_floor_support(np.zeros((1,1),dtype='uint8'),(0.,0.),.1,0.,{'floor'},
            SimpleNamespace(raycast_all=ray),lambda:None)
        self.assertFalse(repaired[0,0])

    def test_furniture_other_floors_and_vertical_faces_are_not_support(self):
        from types import SimpleNamespace
        for body,z,normal in [('chair',0.,(0.,0.,1.)),('floor',-.1,(0.,0.,1.)),
                              ('floor',0.,(1.,0.,0.))]:
            with self.subTest(body=body,z=z,normal=normal):
                query=SimpleNamespace(raycast_all=lambda origin,direction,distance,callback:
                    callback(SimpleNamespace(rigid_body=body,position=(0.,0.,z),normal=normal)))
                repaired,_=repair_floor_support(np.zeros((1,1),dtype='uint8'),(0.,0.),.1,0.,
                    {'floor'},query,lambda:None)
                self.assertFalse(repaired[0,0])


if __name__ == '__main__':
    unittest.main()
