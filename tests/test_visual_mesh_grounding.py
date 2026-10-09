"""Real triangle-ray ownership; rendered depth does not choose an object."""
import importlib.util
import sys
import types
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

from manipulation_agent.contracts import SkillError
from manipulation_agent.executors.visual_mesh_grounding import query_visual_surface


@unittest.skipUnless(importlib.util.find_spec('torch') and importlib.util.find_spec('trimesh'),
                     'Requires torch and trimesh geometry dependencies')
class VisualRayOwnership(unittest.TestCase):
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

    def test_nearest_positive_visual_hit_wins_independent_of_scene_order(self):
        near, near_mesh = self.box('near', [0., 0., 1.])
        far, far_mesh = self.box('far', [0., 0., -2.])
        behind, _ = self.box('behind_camera', [0., 0., 5.])
        off_ray, off_mesh = self.box('off_ray', [10., 0., 0.])
        backend = self.backend([far, behind, off_ray, near])
        owner, result = query_visual_surface(backend, self.origin, self.direction)
        self.assertIs(owner, near)
        self.torch.testing.assert_close(self.torch.tensor(result['visual_triangle_position']),
                                       self.torch.tensor([.1, .15, 1.5]))
        # Coarse bounds must reject meshes before expensive USD conversion.
        self.assertEqual(result['visual_meshes_tested'], 2)
        self.assertCountEqual([call.args[0] for call in self.loader.call_args_list],
                              [far_mesh.prim, near_mesh.prim])
        # A mesh excluded by the first ray remains available to later rays.
        shifted = self.origin+self.torch.tensor([10., 0., 0.])
        self.assertIs(query_visual_surface(backend, shifted, self.direction)[0], off_ray)
        self.assertCountEqual([call.args[0] for call in self.loader.call_args_list],
                              [far_mesh.prim, near_mesh.prim, off_mesh.prim])

    def test_cached_local_geometry_uses_live_transform_without_collision_aabb(self):
        near, mesh = self.box('moving', [0., 0., 1.])
        far, _ = self.box('far', [0., 0., -2.])
        backend = self.backend([far, near])
        self.assertIs(query_visual_surface(backend, self.origin, self.direction)[0], near)
        conversions = self.loader.call_count
        mesh.scaled_transform[0, 3] = 2.
        # A stale collision AABB cannot veto the current visible-mesh pose.
        near.aabb = (self.torch.tensor([40., 40., 40.]), self.torch.tensor([41., 41., 41.]))
        owner, result = query_visual_surface(backend, self.origin+self.torch.tensor([2., 0., 0.]),
                                             self.direction)
        self.assertIs(owner, near)
        self.assertAlmostEqual(result['visual_triangle_position'][0], 2.1, places=5)
        self.assertIs(query_visual_surface(backend, self.origin, self.direction)[0], far)
        self.assertEqual(self.loader.call_count, conversions)

    def test_robot_visual_surface_blocks_objects_behind_it(self):
        robot, _ = self.box('robot', [0., 0., 1.])
        far, _ = self.box('target_behind_robot', [0., 0., -2.])
        with self.assertRaises(SkillError) as error:
            query_visual_surface(self.backend([far, robot], robot), self.origin, self.direction)
        self.assertEqual(error.exception.code, 'invalid_visual_target')

    def test_cloth_owns_occluding_ray_and_new_points_replace_old_geometry(self):
        torch = self.torch
        # ClothPrim is itself the mesh and has no rigid visual_meshes mapping.
        link = SimpleNamespace(visible=True, prim_path='/cloth/link',
            prim=SimpleNamespace(computed_purpose='default'),
            scaled_transform=torch.eye(4),
            points=torch.tensor([[-.5, -.5, 1.], [.5, -.5, 1.],
                                 [.5, .5, 1.], [-.5, .5, 1.]]),
            faces=torch.tensor([[0, 1, 2], [0, 2, 3]]))
        cloth = SimpleNamespace(prim_path='/cloth', links={'cloth': link})
        far, _ = self.box('behind_cloth', [0., 0., -2.])
        backend = self.backend([far, cloth])
        owner, result = query_visual_surface(backend, self.origin, self.direction)
        self.assertIs(owner, cloth)
        self.assertAlmostEqual(result['visual_triangle_position'][2], 1.)
        link.points = link.points + torch.tensor([3., 0., 0.])
        owner, result = query_visual_surface(backend, self.origin, self.direction)
        self.assertIs(owner, far)
        self.assertAlmostEqual(result['visual_triangle_position'][2], -1.5)
        # Only the unchanged rigid box uses cached USD-to-mesh conversion.
        self.assertEqual(self.loader.call_count, 1)

    def test_invisible_mesh_is_not_an_occluder_and_no_forward_hit_fails(self):
        near, _ = self.box('invisible', [0., 0., 1.], visible=False)
        far, _ = self.box('far', [0., 0., -2.])
        self.assertIs(query_visual_surface(self.backend([near, far]), self.origin, self.direction)[0], far)
        with self.assertRaises(SkillError) as error:
            query_visual_surface(self.backend([near]), self.origin, self.direction)
        self.assertEqual(error.exception.code, 'invalid_visual_target')

    def test_guide_fill_volume_does_not_occlude_food_but_container_body_does(self):
        container, helper = self.box('fridge/meta__base_link_fillable', [0., 0., 1.],
                                     purpose='guide')
        food, food_mesh = self.box('egg', [0., 0., -1.])
        _, body = self.box('fridge/door', [2., 0., 1.])
        container.links['door'] = SimpleNamespace(visual_meshes={'mesh': body})
        backend = self.backend([container, food])
        owner, result = query_visual_surface(backend, self.origin, self.direction)
        self.assertIs(owner, food)
        self.assertEqual(result['guide_meshes_skipped'], 1)
        self.assertEqual(result['visual_mesh_purpose'], 'default')
        self.assertEqual([c.args[0] for c in self.loader.call_args_list], [food_mesh.prim])
        # A click on the actual fridge door must still resolve to the fridge.
        self.assertIs(query_visual_surface(backend, self.origin+self.torch.tensor([2., 0., 0.]),
                                          self.direction)[0], container)
        # Visibility/purpose changes must not be hidden by the geometry cache.
        helper.prim.computed_purpose = 'render'
        self.assertIs(query_visual_surface(backend, self.origin, self.direction)[0], container)

    def test_visible_meta_toggle_button_remains_an_occluding_target(self):
        appliance, _ = self.box('appliance/meta__togglebutton', [0., 0., 1.])
        food, _ = self.box('behind_button', [0., 0., -1.])
        self.assertIs(query_visual_surface(self.backend([food, appliance]),
                                          self.origin, self.direction)[0], appliance)


if __name__ == '__main__':
    unittest.main()
