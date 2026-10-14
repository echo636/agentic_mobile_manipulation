"""Assembly placement geometry, collision recovery and transaction behavior."""
import importlib.util
import math
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from manipulation_agent.executors.inside_sampling import assembly_orientations, inset_centers, sample_assembly_inside


class InsetCentersTests(unittest.TestCase):
    def test_every_center_keeps_whole_extent_inside(self):
        points = inset_centers((1., 2., 3.), (2., 4., 6.), (.3, .8, 1.2))
        self.assertEqual(len(points), 27)
        for p in points:
            for value, low, high, extent in zip(p, (1,2,3), (2,4,6), (.3,.8,1.2)):
                self.assertGreater(value-extent/2, low)
                self.assertLess(value+extent/2, high)

    def test_oversized_assembly_has_no_spurious_candidates(self):
        self.assertEqual(inset_centers((0,0,0), (1,1,1), (1.1,.1,.1)), [])


@unittest.skipUnless(importlib.util.find_spec('torch'), 'Requires CPU torch')
class AssemblySamplingTests(unittest.TestCase):
    def fixture(self, *, collide=True, oversize=False):
        import torch
        Inside = type('Inside', (), {})
        class Object:
            def __init__(self, name, position, extent, offset=(0.,0.,0.)):
                self.name=name; self.position=torch.tensor(position); self.quat=torch.tensor([0.,0.,0.,1.])
                self.extent=torch.tensor(extent); self.offset=torch.tensor(offset)
                self.scene=SimpleNamespace(idx=0); self.n_joints=0
                self.states={Inside:SimpleNamespace(get_value=lambda target:all(
                    bool(((self.aabb[0] >= l.visual_aabb[0]) & (self.aabb[1] <= l.visual_aabb[1])).all())
                    for l in target.links.values()))}
            def get_position_orientation(self): return self.position.clone(),self.quat.clone()
            def set_position_orientation(self, position, orientation): self.position=position.clone();self.quat=orientation.clone()
            def keep_still(self): pass
            def get_linear_velocity(self): return torch.zeros(3)
            @property
            def aabb(self):
                center=self.position+self.offset
                return center-self.extent/2,center+self.extent/2
        held=Object('held',[5.,0.,0.],[2.,.2,.2] if oversize else [.2,.2,.2],offset=(.08,0.,.03))
        child=Object('child',[5.08,0.,.03],[.05,.05,.05])
        target=Object('container',[0.,0.,0.],[1.,1.,1.])
        bounds=(torch.tensor([-.5,-.5,0.]),torch.tensor([.5,.5,1.]))
        volume=SimpleNamespace(is_meta_link=True,meta_link_type='fillable',prim_path='volume',visual_aabb=bounds,
            check_points_in_volume=lambda points:((points >= bounds[0]) & (points <= bounds[1])).all(dim=1))
        target.links={'volume':volume}
        state={'steps':0,'loads':0}
        def step(): state['steps']+=1
        def dump(): return [o.get_position_orientation() for o in [held,child]]
        def load(value, **kwargs):
            state['steps']=0;state['loads']+=1
            for obj,pose in zip([held,child],value):obj.set_position_orientation(*pose)
        sim=SimpleNamespace(dump_state=lambda **kw:dump(),load_state=load,step_physics=step,get_physics_dt=lambda:1/120)
        def contact(**kwargs):
            if state['steps'] == 1:
                center=held.position+held.offset
                return collide and (collide=='all' or float(torch.linalg.norm(center[:2])) < .1)
            return True
        records=[]
        backend=SimpleNamespace(torch=torch,og=SimpleNamespace(sim=sim),deadline=SimpleNamespace(check=lambda **kw:None),
            _relocate_contents=lambda root, contents:child.set_position_orientation(root.position+held.offset,root.quat),
            _placement_record=records.append,_placement_contacts=lambda *args:['fixture_obstacle'])
        states=ModuleType('omnigibson.object_states');states.Inside=Inside
        utils=ModuleType('omnigibson.utils')
        def quat_multiply(a,b):
            return torch.cat((a[3]*b[:3]+b[3]*a[:3]+torch.linalg.cross(a[:3],b[:3]),
                              (a[3]*b[3]-torch.dot(a[:3],b[:3])).reshape(1)))
        def quat2mat(q):
            x,y,z,w=q
            return torch.tensor([[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)],
                                 [2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)],
                                 [2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]])
        utils.transform_utils=SimpleNamespace(euler2quat=lambda e:torch.tensor([0.,0.,math.sin(float(e[2])/2),math.cos(float(e[2])/2)]),
            quat_multiply=quat_multiply,quat2mat=quat2mat,get_orientation_diff_in_radian=lambda a,b:0.)
        usd=ModuleType('omnigibson.utils.usd_utils');usd.RigidContactAPI=SimpleNamespace(is_in_contact=contact)
        return backend,held,target,[(child,None)],state,records,{
            'omnigibson.object_states':states,'omnigibson.utils':utils,'omnigibson.utils.usd_utils':usd}

    def test_nearby_resting_pose_corrects_tilt_without_flipping_payload(self):
        b,held,_,_,_,_,modules=self.fixture()
        held.orientations={'upright':{'rotation':[0.,0.,0.,1.],'prob':.8},
                           'inverted':{'rotation':[1.,0.,0.,0.],'prob':.2}}
        tilt=b.torch.tensor([math.sin(math.radians(10)),0.,0.,math.cos(math.radians(10))])
        transforms=modules['omnigibson.utils'].transform_utils
        yaw=transforms.euler2quat(b.torch.tensor([0.,0.,2.4]))
        carried=transforms.quat_multiply(yaw,tilt)
        candidates=assembly_orientations(held,carried,b.torch,transforms)
        stable=[q for label,q,_ in candidates if label=='asset_resting_pose']
        self.assertEqual(len(stable),4)
        for quat in stable:
            self.assertGreater(float(transforms.quat2mat(quat)[2,2]),.999)
        self.assertTrue(any(abs(float(b.torch.dot(q,carried))) > .99999 for _,q,_ in candidates))

    def test_missing_asset_metadata_preserves_carried_up_direction(self):
        b,held,_,_,_,_,modules=self.fixture()
        transforms=modules['omnigibson.utils'].transform_utils
        carried=b.torch.tensor([math.sin(.2),0.,0.,math.cos(.2)])
        for label,quat,_ in assembly_orientations(held,carried,b.torch,transforms):
            self.assertEqual(label,'carried_pose')
            self.assertTrue(b.torch.allclose(transforms.quat2mat(quat)[2],transforms.quat2mat(carried)[2],atol=1e-6))

    def test_occupied_center_searches_alternative_and_preserves_payload(self):
        b,held,target,contents,state,records,modules=self.fixture()
        with patch.dict('sys.modules',modules):
            self.assertTrue(sample_assembly_inside(b,held,target,contents))
        self.assertTrue(any(r.get('stage')=='initial_collision' for r in records))
        self.assertTrue(records[-1]['accepted'])
        self.assertGreater(state['loads'],0)
        self.assertGreater(abs(float(held.position[1])),.1)

    def test_all_collisions_restore_original_pose(self):
        b,held,target,contents,state,records,modules=self.fixture(collide='all')
        original=held.get_position_orientation()
        with patch.dict('sys.modules',modules):
            self.assertFalse(sample_assembly_inside(b,held,target,contents,max_candidates=3))
        self.assertTrue(b.torch.equal(held.position,original[0]))
        self.assertEqual(records[-1]['rejections']['initial_collision'],3)

    def test_oversize_fails_without_physics(self):
        b,held,target,contents,state,records,modules=self.fixture(oversize=True)
        with patch.dict('sys.modules',modules):
            self.assertFalse(sample_assembly_inside(b,held,target,contents))
        self.assertEqual(state['steps'],0)
        self.assertEqual(state['loads'],0)
        self.assertEqual(records[-1]['attempted'],0)
        self.assertEqual(float(held.position[0]),5.)


if __name__=='__main__':unittest.main()
