"""World coordinates and floor limits for action-local map refinement."""
import unittest

import numpy as np

from manipulation_agent.executors.navigation.gt_navigation import GridMap
from manipulation_agent.executors.navigation.live_gt_map import floor_support_window, repair_floor_support


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


class LiveFloorSupportTests(unittest.TestCase):
    def test_recovers_a_doorway_omitted_by_the_traversal_image(self):
        from types import SimpleNamespace
        raw = np.ones((7, 9), dtype='uint8')
        raw[3, :] = 0
        def ray(origin, direction, distance):
            # Structural floor ends at x=.6; the remaining black pixels are
            # a genuine void, even though their neighbours have floor.
            x, y, _ = origin
            return ({'hit': True, 'rigidBody': 'floor', 'normal': (0.,0.,1.),
                     'position': (x,y,0.)} if x < .6 else {'hit': False})
        repaired, details = repair_floor_support(raw,(0.,0.),.1,0.,{'floor'},
            SimpleNamespace(raycast_closest=ray),lambda:None)
        self.assertTrue(repaired[3,3])
        self.assertFalse(repaired[3,7])
        self.assertFalse(raw[3,3])
        self.assertGreater(details['added_cells'],0)

    def test_rejects_a_cell_with_an_unsupported_corner(self):
        from types import SimpleNamespace
        def ray(origin, direction, distance):
            x,y,_=origin
            if x > 0 and y > 0:return {'hit':False}
            return {'hit':True,'rigidBody':'floor','normal':(0.,0.,1.),'position':(x,y,0.)}
        repaired,_=repair_floor_support(np.zeros((1,1),dtype='uint8'),(0.,0.),.1,0.,{'floor'},
            SimpleNamespace(raycast_closest=ray),lambda:None)
        self.assertFalse(repaired[0,0])

    def test_furniture_other_floors_and_vertical_faces_are_not_support(self):
        from types import SimpleNamespace
        for body,z,normal in [('chair',0.,(0.,0.,1.)),('floor',-.1,(0.,0.,1.)),
                              ('floor',0.,(1.,0.,0.))]:
            with self.subTest(body=body,z=z,normal=normal):
                query=SimpleNamespace(raycast_closest=lambda *args:{'hit':True,
                    'rigidBody':body,'position':(0.,0.,z),'normal':normal})
                repaired,_=repair_floor_support(np.zeros((1,1),dtype='uint8'),(0.,0.),.1,0.,
                    {'floor'},query,lambda:None)
                self.assertFalse(repaired[0,0])


if __name__ == '__main__':
    unittest.main()
