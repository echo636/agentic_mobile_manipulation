"""Interior contact proposals without filling holes or changing shelf height."""
import unittest
import numpy as np
from manipulation_agent.executors.surface_geometry import overlapping_surface_probes


def rectangle(x0, x1, y0, y1, z):
    a, b, c, d = [[x0,y0,z], [x1,y0,z], [x1,y1,z], [x0,y1,z]]
    return np.array([[a,b,c],[a,c,d]])


class SurfaceOverlapTests(unittest.TestCase):
    def test_narrow_rim_crossing_face_interior_without_vertex_or_center_support(self):
        bottom=rectangle(-1.,1.,-1.,1.,.2)
        rim=rectangle(-.3,.3,.13,.15,.2)
        points=overlapping_surface_probes(bottom,rim,.2)
        self.assertGreater(len(points),0)
        self.assertTrue(np.all((points[:,0]>=-.3)&(points[:,0]<=.3)))
        self.assertTrue(np.all((points[:,1]>=.13)&(points[:,1]<=.15)))
        np.testing.assert_allclose(points[:,2],.2)

    def test_empty_space_and_another_shelf_do_not_produce_support(self):
        bottom=rectangle(-1.,1.,-1.,1.,.2)
        for support in (rectangle(1.1,2.,-.1,.1,.2),rectangle(-.3,.3,-.1,.1,.1)):
            self.assertEqual(len(overlapping_surface_probes(bottom,support,.2)),0)
