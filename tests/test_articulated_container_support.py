"""Sliding shelves must carry their contents without ignoring door collisions."""
import importlib.util
from contextlib import ExitStack
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from manipulation_agent.executors.carry import ControlledCarry
from manipulation_agent.executors.closure_clearance import check_closure_clearance
from manipulation_agent.executors.container_support import contacting_support


@unittest.skipUnless(importlib.util.find_spec('torch'), 'Requires CPU torch')
class ArticulatedSupportTests(unittest.TestCase):
    def fixture(self, *, door_collision=False):
        import numpy as np
        import torch
        class Inside: pass
        class Open: pass
        class Body:
            def __init__(self, name, position, half=(.1,.1,.025)):
                self.name=name;self.prim_path='/'+name
                self.position=torch.tensor(position,dtype=torch.float32)
                self.half=torch.tensor(half);self.fixed_base=False;self.n_joints=0
                self.is_meta_link=False;self.scene=SimpleNamespace(idx=0)
                self.links={name:self};self.states={}
            @property
            def aabb(self):return self.position-self.half,self.position+self.half
            def get_position_orientation(self):return self.position.clone(),torch.tensor([0.,0.,0.,1.])
            def set_position_orientation(self,p,q):self.position=p.clone()
            def keep_still(self):pass
        oven=Body('oven',[0,0,.5],(3,3,3));oven.n_joints=2
        rack=Body('rack',[0,0,.5],(.5,.5,.025))
        fixed=Body('fixed',[2,0,.5],(.5,.5,.025))
        door=Body('door',[0,1,.5]);volume=Body('volume',[0,0,.5])
        volume.is_meta_link=True;volume.meta_link_type='fillable'
        volume.check_points_in_volume=lambda points:torch.ones(len(points),dtype=torch.bool)
        oven.links={o.name:o for o in [rack,fixed,door,volume]}
        tray=Body('tray',[0,0,.55]);food=Body('food',[0,0,.6])
        other=Body('other',[2,0,.55]);robot=Body('robot',[10,10,0])
        children=[tray,food,other];bodies=[oven,rack,fixed,door,volume,*children]
        for child in children:child.states={Inside:SimpleNamespace(get_value=lambda target:True)}
        state={'closed':False,'door_collision':door_collision}
        def close(value,fully=False):
            state['closed']=not value;rack.position[0]=.2 if state['closed'] else 0.
            door.position[1]=0. if state['closed'] else 1.
            return True
        oven.states={Open:SimpleNamespace(get_value=lambda:not state['closed'],set_value=close)}
        supports={tray:rack,food:tray,other:fixed}
        def pairs(idx,query_set,with_set,current_only):
            out=[(child.prim_path,support.prim_path) for child,support in supports.items()]
            if state['closed'] and state['door_collision']:out.append((tray.prim_path,door.prim_path))
            if query_set==[oven]:
                return [(a,z) for a,z in out if z in {rack.prim_path,fixed.prim_path,door.prim_path}
                        and a in {obj.prim_path for obj in with_set}]
            return [(a,z) for a,z in out if a==query_set[0].prim_path or z==query_set[0].prim_path]
        def ray(start,end,ignore_bodies):
            hits=[]
            for obj in [rack,fixed,tray]:
                if obj.prim_path in ignore_bodies:continue
                lo,hi=obj.aabb
                if all(float(lo[i])<=start[i]<=float(hi[i]) for i in (0,1)) and end[2]<=float(hi[2])<=start[2]:
                    hits.append((float(hi[2]),obj))
            if not hits:return {'hit':False}
            z,obj=max(hits,key=lambda row:row[0])
            return dict(hit=True,rigidBody=obj.prim_path,position=np.array([start[0],start[1],z]),normal=np.array([0,0,1]))
        def vertices(obj):
            lo,hi=obj.aabb
            return np.array([[x,y,z] for x in (lo[0],hi[0]) for y in (lo[1],hi[1]) for z in (lo[2],hi[2])])
        def dump(**kwargs):return state['closed'],[o.get_position_orientation() for o in bodies]
        def load(saved,**kwargs):
            state['closed']=saved[0]
            for obj,pose in zip(bodies,saved[1]):obj.set_position_orientation(*pose)
        b=ControlledCarry();b.torch=torch;b.robot=robot
        b.env=SimpleNamespace(scene=SimpleNamespace(objects=[oven,*children,robot]))
        b.og=SimpleNamespace(sim=SimpleNamespace(dump_state=dump,load_state=load,step_physics=lambda:None))
        b.deadline=SimpleNamespace(check=lambda **kw:None);b._collision_vertices=vertices
        modules={name:ModuleType(name) for name in ['omnigibson.object_states','omnigibson.utils',
            'omnigibson.utils.usd_utils','omnigibson.utils.sampling_utils']}
        modules['omnigibson.object_states'].Inside=Inside;modules['omnigibson.object_states'].Open=Open
        modules['omnigibson.utils'].transform_utils=SimpleNamespace(
            relative_pose_transform=lambda p,q,b,bq:(p-b,q),pose_transform=lambda b,bq,p,q:(b+p,q))
        modules['omnigibson.utils.usd_utils'].RigidContactAPI=SimpleNamespace(get_contact_pairs=pairs)
        modules['omnigibson.utils.sampling_utils'].raytest=ray
        return b,oven,rack,children,modules,state

    def test_nested_contents_follow_shelf_and_stationary_contents_stay(self):
        b,oven,rack,(tray,food,other),modules,_=self.fixture()
        with patch.dict('sys.modules',modules):
            payload=b._container_payload(oven)
            self.assertIs(next(frame for obj,frame,_ in payload if obj is tray),rack)
            self.assertIs(next(frame for obj,frame,_ in payload if obj is food),rack)
            rack.position[0]=.2;b._relocate_container_payload(payload)
        self.assertAlmostEqual(float(tray.position[0]),.2,places=6)
        self.assertAlmostEqual(float(food.position[0]),.2,places=6)
        self.assertEqual(float(other.position[0]),2.)

    def test_contact_and_upward_support_are_both_required(self):
        b,oven,_,(tray,_,_),modules,_=self.fixture()
        with patch.dict('sys.modules',modules):
            api=modules['omnigibson.utils.usd_utils'].RigidContactAPI
            with patch.object(api,'get_contact_pairs',return_value=[]):
                self.assertIsNone(contacting_support(b,tray,[oven]))
            sampling=modules['omnigibson.utils.sampling_utils']
            with patch.object(sampling,'raytest',return_value=dict(hit=True,rigidBody='/rack',
                    position=[0,0,.525],normal=[1,0,0])):
                self.assertIsNone(contacting_support(b,tray,[oven]))

    def test_closed_support_contact_allowed_but_new_door_contact_rejected(self):
        for collision in (False,True):
            with self.subTest(door_collision=collision):
                b,oven,rack,(tray,_,_),modules,state=self.fixture(door_collision=collision)
                with ExitStack() as stack:
                    stack.enter_context(patch.dict('sys.modules',modules))
                    for name,value in [('snapshot_particle_state',[]),('particle_state_unchanged',True),
                                       ('restore_particle_state',None)]:
                        stack.enter_context(patch('manipulation_agent.executors.particle_payload.'+name,return_value=value))
                    stack.enter_context(patch('manipulation_agent.executors.placement.scene_object_identities',return_value=()))
                    result=check_closure_clearance(b,oven,[tray])
                self.assertEqual(result['clear'],not collision)
                self.assertIn(['/rack','/tray'],result['preserved_support_contacts'])
                self.assertEqual(bool(result['blocking_contacts']),collision)
                self.assertFalse(state['closed'])
                self.assertEqual(float(rack.position[0]),0.)
                self.assertEqual(float(tray.position[0]),0.)


if __name__=='__main__':unittest.main()
