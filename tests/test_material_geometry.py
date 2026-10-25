import importlib.util
import unittest

from manipulation_agent.executors.material_geometry import convex_sphere_overlap


@unittest.skipUnless(importlib.util.find_spec('trimesh') and importlib.util.find_spec('scipy'),
                     'Requires collision geometry environment')
class ConvexMaterialContactTests(unittest.TestCase):
    def test_contacts_distinguish_face_corner_and_inside(self):
        import numpy as np
        import trimesh
        from scipy.spatial import ConvexHull
        mesh = trimesh.creation.box(extents=[2., 2., 2.])
        equations = ConvexHull(mesh.vertices).equations
        # An expanded-box proxy rejects the diagonal gap near this corner.
        self.assertFalse(convex_sphere_overlap(mesh, equations, np.array([[1.09, 1.09, 0.]]), [.1]))
        self.assertTrue(convex_sphere_overlap(mesh, equations, np.array([[1.05, 1.05, 0.]]), [.1]))
        self.assertTrue(convex_sphere_overlap(mesh, equations, np.array([[1.05, 0., 0.]]), [.1]))
        self.assertTrue(convex_sphere_overlap(mesh, equations, np.array([[0., 0., 0.]]), [.1]))
        self.assertFalse(convex_sphere_overlap(mesh, equations, np.empty((0, 3)), []))
