import unittest
import numpy as np

from manipulation_agent.cloth_compatibility import missing_vertex_normals


class MissingClothNormalsTests(unittest.TestCase):
    def test_quad_winding_generates_finite_unit_vertex_normals(self):
        points = np.array([[0, 0, 0], [2, 0, 0], [2, 1, 0], [0, 1, 0]], dtype=float)
        before = points.copy()
        normals = missing_vertex_normals(points, [4], [0, 1, 2, 3])
        np.testing.assert_allclose(normals, np.tile([0, 0, 1], (4, 1)))
        np.testing.assert_array_equal(points, before)

    def test_invalid_index_is_rejected_before_mesh_conversion(self):
        with self.assertRaisesRegex(ValueError, 'absent vertex'):
            missing_vertex_normals([[0, 0, 0], [1, 0, 0], [0, 1, 0]], [3], [0, 1, 8])

    def test_incomplete_face_data_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Malformed cloth face'):
            missing_vertex_normals([[0, 0, 0], [1, 0, 0], [0, 1, 0]], [3], [0, 1])


if __name__ == '__main__':
    unittest.main()
