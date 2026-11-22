"""Reference surface selection against occluding cabinetry; no simulator startup."""
import importlib.util
import sys
from types import ModuleType,SimpleNamespace
import unittest
from unittest.mock import patch

from manipulation_agent.references.full_workflow_reference import FullWorkflowReference


@unittest.skipUnless(importlib.util.find_spec('torch'),'Requires pinned CPU tensor dependency')
class RecessedApplianceSurfaces(unittest.TestCase):
    def test_countertop_and_neighbor_do_not_force_hidden_aabb_face(self):
        import torch
        objects=[('/washer/body',[0.,0.,0.],[1.,1.,1.]),
                 ('/countertop',[-.1,-.1,.95],[1.1,1.1,1.05]),
                 ('/neighbor',[0.,1.01,0.],[1.,2.,1.1])]
        def raytest(start,end,**kwargs):
            hits=[]
            for path,low,high in objects:
                enter,leave=0.,1.
                for a,b,lo,hi in zip(start.tolist(),end.tolist(),low,high):
                    delta=b-a
                    if abs(delta)<1e-10:
                        if not lo<=a<=hi:leave=-1.;break
                    else:
                        u,v=sorted(((lo-a)/delta,(hi-a)/delta))
                        enter=max(enter,u);leave=min(leave,v)
                if enter<=leave:hits.append((enter,path))
            if not hits:return {'hit':False}
            t,path=min(hits)
            return dict(hit=True,rigidBody=path,position=start+t*(end-start))
        module=ModuleType('omnigibson.utils.sampling_utils');module.raytest=raytest
        washer=SimpleNamespace(aabb=(torch.tensor([0.,0.,0.]),torch.tensor([1.,1.,1.])),
                               links={'body':SimpleNamespace(prim_path='/washer/body')})
        flow=FullWorkflowReference.__new__(FullWorkflowReference)
        flow.ref=SimpleNamespace(obj=lambda _:washer)
        flow.backend=SimpleNamespace(torch=torch,rig_height=1.5,_get_held=lambda:None,
            robot=SimpleNamespace(links={},get_position_orientation=lambda:(torch.tensor([.5,5.,0.]),None)))
        with patch.dict(sys.modules,{'omnigibson.utils.sampling_utils':module}):
            point=flow.surface_point('washer','open')
        self.assertLess(point[2],.95)
        self.assertAlmostEqual(point[1],.5)
        self.assertTrue(abs(point[0])<1e-6 or abs(point[0]-1.)<1e-6)
        origin=torch.tensor(point);origin[0]+= -.2 if point[0]<.5 else .2
        hit=raytest(origin,torch.tensor(point))
        self.assertEqual(hit['rigidBody'],'/washer/body')

    def test_place_inside_prefers_nearest_surface_over_far_corner_at_eye_height(self):
        import torch
        # Cabinet spans x in [0, 1], y in [0, 1], z in [0, 2]; robot stands near y=2.14 (1.14 m from near face y=1).
        # Far side x=0 / y=0 hits match eye height (1.4 m) slightly closer than a near-face hit at z=1.3 m,
        # yet place_inside must choose the near face (y=1.0) so it does not trigger a spurious approach.
        def raytest(start,end,**kwargs):
            s=start.tolist();e=end.tolist()
            if s[1]>1.5 and e[1]<=1.0:
                return dict(hit=True,rigidBody='/cab/body',position=torch.tensor([0.5,1.0,1.3]))
            if s[1]<0.0:
                return dict(hit=True,rigidBody='/cab/body',position=torch.tensor([0.5,0.0,1.4]))
            return {'hit':False}
        module=ModuleType('omnigibson.utils.sampling_utils');module.raytest=raytest
        cabinet=SimpleNamespace(aabb=(torch.tensor([0.,0.,0.]),torch.tensor([1.,1.,2.])),
                                links={'body':SimpleNamespace(prim_path='/cab/body')})
        flow=FullWorkflowReference.__new__(FullWorkflowReference)
        flow.ref=SimpleNamespace(obj=lambda _:cabinet)
        flow.backend=SimpleNamespace(torch=torch,rig_height=1.5,_get_held=lambda:None,
            robot=SimpleNamespace(links={},get_position_orientation=lambda:(torch.tensor([0.5,2.14,0.]),None)))
        with patch.dict(sys.modules,{'omnigibson.utils.sampling_utils':module}):
            point=flow.surface_point('cabinet','place_inside')
        self.assertAlmostEqual(point[1],1.0)


if __name__=='__main__':unittest.main()
