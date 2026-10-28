"""Run with Isaac's CPU USD library; these checks do not launch a simulator."""
import unittest

try:
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade
except ImportError:
    Usd = None

from manipulation_agent.executors.compat.cloth_import import prepare_nested_cloth_visual


@unittest.skipIf(Usd is None, 'Requires the pinned USD Python library')
class ClothImportTests(unittest.TestCase):
    def setUp(self):
        self.stage = Usd.Stage.CreateInMemory()
        self.root = UsdGeom.Xform.Define(self.stage, '/towel').GetPrim()
        self.link = UsdGeom.Xform.Define(self.stage, '/towel/base_link').GetPrim()
        self.copies = []

    def copy(self, source, destination):
        self.copies.append((str(source), str(destination)))
        layer = self.stage.GetRootLayer()
        self.assertTrue(Sdf.CopySpec(layer, source, layer, destination))

    def test_nested_visual_preserves_geometry_transform_and_inherited_material(self):
        UsdGeom.Xformable(self.root).AddTranslateOp().Set((7, 8, 9))
        UsdGeom.Xformable(self.link).AddRotateZOp().Set(30)
        group = UsdGeom.Xform.Define(self.stage, '/towel/base_link/visuals')
        group.AddTranslateOp().Set((1, 2, 3))
        group.AddScaleOp().Set((2, 3, 4))
        mesh = UsdGeom.Mesh.Define(self.stage, '/towel/base_link/visuals/mesh')
        mesh.CreatePointsAttr([(0, 0, 0), (1, 0, 0), (0, 1, 0)])
        mesh.CreateFaceVertexCountsAttr([3])
        mesh.CreateFaceVertexIndicesAttr([0, 1, 2])
        mesh.AddTranslateOp().Set((.1, .2, .3))
        material = UsdShade.Material.Define(self.stage, '/towel/Looks/material')
        UsdShade.MaterialBindingAPI.Apply(group.GetPrim()).Bind(material)
        collider = UsdGeom.Mesh.Define(self.stage, '/towel/base_link/collisions/mesh')
        UsdPhysics.CollisionAPI.Apply(collider.GetPrim())
        world = UsdGeom.XformCache().GetLocalToWorldTransform(mesh.GetPrim())
        result = prepare_nested_cloth_visual(self.root, self.copy)
        self.assertEqual(len(self.copies), 1)
        # Model the native importer's final copy and old-link deletion.
        self.copy(Sdf.Path(result['prepared_mesh']), Sdf.Path('/towel/base_link_cloth'))
        self.stage.RemovePrim('/towel/base_link')
        copied = self.stage.GetPrimAtPath('/towel/base_link_cloth')
        self.assertTrue(Gf.IsClose(world, UsdGeom.XformCache().GetLocalToWorldTransform(copied), 1e-9))
        self.assertEqual(list(UsdGeom.Mesh(copied).GetFaceVertexIndicesAttr().Get()), [0, 1, 2])
        self.assertEqual(UsdShade.MaterialBindingAPI(copied).ComputeBoundMaterial()[0].GetPath(), material.GetPath())
        self.assertFalse(copied.HasAPI(UsdPhysics.CollisionAPI))

    def test_direct_asset_unchanged(self):
        UsdGeom.Mesh.Define(self.stage, '/towel/base_link/mesh')
        before = self.stage.GetRootLayer().ExportToString()
        self.assertIsNone(prepare_nested_cloth_visual(self.root, self.copy))
        self.assertEqual(self.stage.GetRootLayer().ExportToString(), before)
        self.assertEqual(self.copies, [])

    def test_ambiguous_visuals_fail_without_copy(self):
        for name in ['a', 'b']:
            UsdGeom.Mesh.Define(self.stage, '/towel/base_link/visuals/' + name)
        with self.assertRaisesRegex(ValueError, 'found 2'):
            prepare_nested_cloth_visual(self.root, self.copy)
        self.assertEqual(self.copies, [])

    def test_collision_only_fails_without_copy(self):
        collider = UsdGeom.Mesh.Define(self.stage, '/towel/base_link/collisions/mesh')
        UsdPhysics.CollisionAPI.Apply(collider.GetPrim())
        with self.assertRaisesRegex(ValueError, 'found 0'):
            prepare_nested_cloth_visual(self.root, self.copy)
        self.assertEqual(self.copies, [])
