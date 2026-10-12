"""Point-to-surface ownership using the depth-backprojected spatial location."""
import importlib.util
import json
import sys
import types
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

from manipulation_agent.contracts import SkillError
from manipulation_agent.executors.visual_mesh_grounding import query_visual_surface


@unittest.skipUnless(importlib.util.find_spec('torch') and importlib.util.find_spec('trimesh'),
                     'Requires torch and trimesh geometry dependencies')
class VisualPointOwnership(unittest.TestCase):
    def setUp(self):
        import torch
        import trimesh
        self.torch = torch
        self.geometry = trimesh.creation.box()
        source = types.ModuleType('omnigibson.utils.usd_utils')
        self.loader = source.mesh_prim_to_trimesh_mesh = Mock(return_value=self.geometry)
        lazy = types.ModuleType('omnigibson.lazy')
        lazy.pxr = SimpleNamespace(UsdGeom=SimpleNamespace(Imageable=lambda prim:
            SimpleNamespace(ComputePurpose=lambda: prim.computed_purpose)))
        modules = {'omnigibson': types.ModuleType('omnigibson'),
                   'omnigibson.lazy': lazy,
                   'omnigibson.utils': types.ModuleType('omnigibson.utils'),
                   'omnigibson.utils.usd_utils': source}
        override = patch.dict(sys.modules, modules)
        override.start()
        self.addCleanup(override.stop)
        self.origin = torch.tensor([.1, .15, 3.])
        self.direction = torch.tensor([0., 0., -1.])

    def box(self, name, center, *, visible=True, purpose='default'):
        torch = self.torch
        center = torch.tensor(center, dtype=torch.float32)
        transform = torch.eye(4)
        transform[:3, 3] = center
        mesh = SimpleNamespace(visible=visible, scaled_transform=transform,
                               prim_path='/'+name+'/mesh', prim=SimpleNamespace(computed_purpose=purpose),
                               points=torch.tensor(self.geometry.vertices.copy(), dtype=torch.float32))
        return SimpleNamespace(prim_path='/'+name, aabb=(center-.5, center+.5),
            links={'link': SimpleNamespace(visual_meshes={'mesh': mesh})}), mesh

    def backend(self, objects, robot=None):
        return SimpleNamespace(robot=robot if robot is not None else SimpleNamespace(links={}),
                               env=SimpleNamespace(scene=SimpleNamespace(objects=objects)))

    def test_surface_point_selects_owner_independent_of_order_or_centers(self):
        near, _ = self.box('near', [0., 0., 1.])
        far, _ = self.box('far', [0., 0., -2.])
        backend = self.backend([far, near])
        point = self.torch.tensor([.1, .15, 1.5])
        owner, result = query_visual_surface(backend, point)
        self.assertIs(owner, near)
        self.assertLess(result['surface_point_distance_m'], 1e-7)
        self.assertEqual(result['visual_meshes_tested'], 1)
        self.assertIs(query_visual_surface(backend, self.torch.tensor([0., 0., -1.5]))[0], far)

    def test_nearest_surface_not_nearest_object_center_or_containing_box(self):
        outer, mesh = self.box('large_hollow', [0., 0., 0.])
        mesh.scaled_transform[:3, :3] *= 10
        inner, _ = self.box('inner', [1., 0., 0.])
        point = self.torch.tensor([.5, 0., 0.])
        self.assertIs(query_visual_surface(self.backend([outer, inner]), point)[0], inner)

    def test_world_metric_respects_nonuniform_scale_and_live_transform(self):
        stretched, mesh = self.box('stretched', [0., 0., 0.])
        mesh.scaled_transform[0, 0] = 100.
        other, other_mesh = self.box('other', [54., 0., 0.])
        backend = self.backend([stretched, other])
        point = self.torch.tensor([53., 0., 0.])
        # Local distance to stretched box is .03, but WORLD distance is 3m.
        # Other's world distance is .5m, so it must win.
        self.assertIs(query_visual_surface(backend, point)[0], other)
        other_mesh.scaled_transform[0, 3] = 60.
        self.assertIs(query_visual_surface(backend, point)[0], stretched)

    def test_robot_surface_rejected_without_selecting_object_behind(self):
        robot, _ = self.box('robot', [0., 0., 1.])
        far, _ = self.box('far', [0., 0., -2.])
        with self.assertRaises(SkillError) as error:
            query_visual_surface(self.backend([far, robot], robot), self.torch.tensor([0., 0., 1.5]))
        self.assertEqual(error.exception.code, 'invalid_visual_target')

    def test_cloth_uses_current_deformed_vertices(self):
        torch = self.torch
        link = SimpleNamespace(visible=True, prim_path='/cloth/link',
            prim=SimpleNamespace(computed_purpose='default'), scaled_transform=torch.eye(4),
            points=torch.tensor([[-.5, -.5, 1.], [.5, -.5, 1.], [.5, .5, 1.], [-.5, .5, 1.]]),
            faces=torch.tensor([[0, 1, 2], [0, 2, 3]]))
        cloth = SimpleNamespace(prim_path='/cloth', links={'cloth': link})
        far, _ = self.box('far', [0., 0., -2.])
        backend = self.backend([far, cloth])
        self.assertIs(query_visual_surface(backend, torch.tensor([0., 0., 1.]))[0], cloth)
        link.points = link.points + torch.tensor([10., 0., 0.])
        self.assertIs(query_visual_surface(backend, torch.tensor([0., 0., -1.5]))[0], far)

    def test_guide_volume_excluded_but_visible_meta_button_preserved(self):
        helper, _ = self.box('fridge/fill', [0., 0., 0.], purpose='guide')
        food, _ = self.box('egg', [.2, 0., 0.])
        point = self.torch.tensor([.5, 0., 0.])
        owner, result = query_visual_surface(self.backend([helper, food]), point)
        self.assertIs(owner, food)
        self.assertEqual(result['guide_meshes_skipped'], 1)
        button, _ = self.box('appliance/button', [0., 0., 0.])
        self.assertIs(query_visual_surface(self.backend([food, button]), point)[0], button)

    def test_invisible_mesh_excluded_and_no_geometry_reports_error(self):
        hidden, _ = self.box('hidden', [0., 0., 0.], visible=False)
        with self.assertRaises(SkillError):
            query_visual_surface(self.backend([hidden]), self.torch.zeros(3))


    def test_collapsed_and_nonfinite_faces_do_not_hide_valid_surface(self):
        import numpy as np
        import trimesh
        valid = self.geometry
        # Preserve malformed triangles instead of mocking the solver's output.
        extra = np.array([[0., 0., 0.], [np.nan, 0., 0.]])
        self.geometry = trimesh.Trimesh(
            vertices=np.vstack([valid.vertices, extra]),
            faces=np.vstack([valid.faces, [8, 8, 8], [0, 1, 9]]), process=False)
        self.loader.return_value = self.geometry
        owner, _ = self.box('real_surface', [0., 0., 0.])
        selected, result = query_visual_surface(self.backend([owner]), self.torch.tensor([.5, 0., 0.]))
        self.assertIs(selected, owner)
        self.assertLess(result['surface_point_distance_m'], 1e-7)
        self.assertEqual(result['invalid_triangles_skipped'], 2)
        json.dumps(result, allow_nan=False)

    def test_invalid_first_mesh_cannot_win_over_nearest_valid_object(self):
        import numpy as np
        import trimesh
        bad, mesh = self.box('bad', [0., 0., 0.])
        mesh.scaled_transform[:3, :3] *= 10
        good, _ = self.box('good', [0., 0., 0.])
        broken = trimesh.Trimesh(
            vertices=np.vstack([self.geometry.vertices, [np.nan, 0., 0.]]),
            faces=np.vstack([self.geometry.faces, [0, 1, 8]]), process=False)
        self.loader.side_effect = [broken, self.geometry]
        selected, result = query_visual_surface(self.backend([bad, good]), self.torch.tensor([.5, 0., 0.]))
        self.assertIs(selected, good)
        self.assertEqual(result['invalid_triangles_skipped'], 1)
        json.dumps(result, allow_nan=False)

    def test_nonfinite_solver_results_do_not_poison_other_finite_faces(self):
        import numpy as np
        import trimesh
        owner, _ = self.box('object', [0., 0., 0.])
        original = trimesh.triangles.closest_point
        def partly_invalid(triangles, points):
            result = original(triangles, points)
            result[0] = np.nan
            return result
        with patch.object(trimesh.triangles, 'closest_point', side_effect=partly_invalid):
            selected, result = query_visual_surface(self.backend([owner]), self.torch.tensor([.5, 0., 0.]))
        self.assertIs(selected, owner)
        self.assertLess(result['surface_point_distance_m'], 1e-7)
        self.assertEqual(result['invalid_surface_points_skipped'], 1)

    def test_no_finite_surface_reports_existing_target_error(self):
        import numpy as np
        import trimesh
        self.loader.return_value = trimesh.Trimesh(
            vertices=np.zeros((3, 3)), faces=[[0, 1, 2]], process=False)
        owner, _ = self.box('collapsed', [0., 0., 0.])
        with self.assertRaises(SkillError) as error:
            query_visual_surface(self.backend([owner]), self.torch.zeros(3))
        self.assertEqual(error.exception.code, 'invalid_visual_target')
        self.assertEqual(error.exception.diagnostics['invalid_triangles_skipped'], 1)
        json.dumps(error.exception.diagnostics, allow_nan=False)

    def test_macro_particle_surface_is_not_attributed_to_fixed_countertop(self):
        torch = self.torch
        table, _ = self.box('countertop', [0., 0., -.5])
        _, particle = self.box('particle', [0., 0., 0.])
        particle.scale = torch.tensor([.02, .02, .02])
        system = SimpleNamespace(name='popcorn', particles={'p': particle}, particle_object=particle,
            particles_view=SimpleNamespace(prim_paths=[particle.prim_path],
                get_transforms=lambda: torch.tensor([[0., 0., .04, 0., 0., 0., 1.]])))
        backend = self.backend([table])
        backend.env.scene.active_systems = {'popcorn': system}
        transform = types.ModuleType('omnigibson.utils.transform_utils')
        transform.quat2mat = lambda q: torch.eye(3).expand(len(q), 3, 3)
        with patch.dict(sys.modules, {'omnigibson.utils.transform_utils': transform}):
            with self.assertRaises(SkillError) as error:
                query_visual_surface(backend, torch.tensor([0., 0., .05]), reject_particles=True)
            self.assertEqual(error.exception.code, 'particle_target')
            self.assertLess(error.exception.diagnostics['surface_point_distance_m'], 1e-7)
            # A visible table pixel still belongs to the fixed table.
            owner, _ = query_visual_surface(backend, torch.tensor([.3, .3, 0.]), reject_particles=True)
            self.assertIs(owner, table)
            # Material actions continue to ground their supporting surface.
            self.assertIs(query_visual_surface(backend, torch.tensor([0., 0., .05]))[0], table)


if __name__ == '__main__':
    unittest.main()
