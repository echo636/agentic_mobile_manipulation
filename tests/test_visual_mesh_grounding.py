import importlib.util
import sys
import types
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from manipulation_agent.contracts import SkillError
from manipulation_agent.executors.visual_mesh_grounding import query_visual_surface

@unittest.skipUnless(importlib.util.find_spec('torch') and importlib.util.find_spec('trimesh'),'Requires motor environment geometry dependencies')
class VisualRayOwnership(unittest.TestCase):
    def test_exact_depth_ray_and_live_transform_without_semantic_lookup(self):
        import torch
        import trimesh
        geometry=trimesh.creation.box()
        transform=torch.eye(4);transform[0,3]=2
        mesh=SimpleNamespace(visible=True,scaled_transform=transform,prim_path='/object/mesh',prim=object())
        obj=SimpleNamespace(aabb=(torch.tensor([1.5,-.5,-.5]),torch.tensor([2.5,.5,.5])),links={'link':SimpleNamespace(visual_meshes={'mesh':mesh})})
        backend=SimpleNamespace(robot=object(),env=SimpleNamespace(scene=SimpleNamespace(objects=[obj])))
        source=types.ModuleType('omnigibson.utils.usd_utils');source.mesh_prim_to_trimesh_mesh=lambda *a,**k:geometry
        with patch.dict(sys.modules,{'omnigibson':types.ModuleType('omnigibson'),'omnigibson.utils':types.ModuleType('omnigibson.utils'),'omnigibson.utils.usd_utils':source}):
            origin=torch.tensor([2.,0.,3.]);direction=torch.tensor([0.,0.,-1.]);point=torch.tensor([2.,0.,.5])
            owner,result=query_visual_surface(backend,origin,direction,point)
            self.assertIs(owner,obj);self.assertLess(result['visual_depth_agreement_error_m'],1e-6)
            # A geometry hit alone is insufficient when the rendered depth disagrees.
            with self.assertRaises(SkillError):query_visual_surface(backend,origin,direction,torch.tensor([2.,0.,.3]))
            transform[0,3]=3;obj.aabb=(torch.tensor([2.5,-.5,-.5]),torch.tensor([3.5,.5,.5]))
            owner,_=query_visual_surface(backend,torch.tensor([3.,0.,3.]),direction,torch.tensor([3.,0.,.5]))
            self.assertIs(owner,obj)
