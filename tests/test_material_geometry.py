import importlib.util
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

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


@unittest.skipUnless(importlib.util.find_spec('torch'), 'Requires particle geometry environment')
class MacroSurfacePoseTests(unittest.TestCase):
    def setUp(self):
        import torch
        self.torch = torch
        self.system = SimpleNamespace(
            particle_object=SimpleNamespace(scale=1., get_attribute=lambda name:
                [[-.04, -.002, -.002], [.04, .002, .002]]), max_scale=1.,
            _particle_offset=torch.tensor([.015, 0., 0.]),
            particle_radius=.041, particle_particle_rest_distance=.082)
        self.destination = SimpleNamespace(aabb=(torch.zeros(3), torch.ones(3)))
        self.sampling = ModuleType('omnigibson.utils.sampling_utils')
        self.sampler = self.sampling.sample_cuboid_on_object_full_grid_topdown = Mock()
        transforms = ModuleType('omnigibson.utils.transform_utils')
        rotation = torch.tensor([[0., -1., 0.], [1., 0., 0.], [0., 0., 1.]])
        transforms.quat2mat = lambda q: rotation.expand(len(q), 3, 3)
        self.modules = patch.dict(sys.modules, {
            'omnigibson': ModuleType('omnigibson'),
            'omnigibson.utils': ModuleType('omnigibson.utils'),
            'omnigibson.utils.transform_utils': transforms,
            'omnigibson.utils.sampling_utils': self.sampling})

    def sample(self, x):
        return (self.torch.tensor([x, 0., 1.]), None,
                self.torch.tensor([0., 0., .70710678, .70710678]), None, {})

    def test_native_box_fit_keeps_correct_particle_frame_without_macro_sphere_rejection(self):
        from manipulation_agent.executors.material_geometry import macro_surface_poses
        # Native box sampling already checked this slender rigid obstacle.
        # Its enclosing sphere must not veto that admitted placement again.
        macro = SimpleNamespace(n_particles=1, particles_view=object(),
            get_particles_position_orientation=Mock(side_effect=AssertionError('duplicate sphere approximation')))
        scene = SimpleNamespace(active_systems={'macro': macro})
        self.sampler.return_value = [self.sample(0.)]
        with self.modules:
            positions, orientations = macro_surface_poses(scene, self.destination, self.system, 1, diagnostics={})
        self.torch.testing.assert_close(positions, self.torch.tensor([[0., .015, 1.]]))
        self.torch.testing.assert_close(self.sampler.call_args.kwargs['cuboid_dimensions'],
                                       self.torch.tensor([.08, .004, .004]))
        self.assertEqual(tuple(orientations.shape), (1, 4))
        macro.get_particles_position_orientation.assert_not_called()

    def test_surface_geometry_keeps_pbd_clearance_and_new_particle_spacing(self):
        from manipulation_agent.executors.material_geometry import macro_surface_poses
        micro = SimpleNamespace(n_particles=1, particle_radius=.01,
            get_particles_position_orientation=lambda: (self.torch.tensor([[0., .015, 1.]]), None))
        scene = SimpleNamespace(active_systems={'micro': micro})
        self.sampler.return_value = [self.sample(x) for x in (0., .01, .2, .21)]
        with self.modules, patch('manipulation_agent.executors.particle_view.physical_particle_view', return_value=object()):
            positions, _ = macro_surface_poses(scene, self.destination, self.system, 4, diagnostics={})
        self.assertEqual(len(positions), 1)
        self.assertGreater(float(positions[0, 0]), .19)
