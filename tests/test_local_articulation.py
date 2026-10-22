"""Door preview rollback and swept geometry; no task-success claim."""
import importlib.util
import random
import sys
import unittest
from contextlib import nullcontext
from types import SimpleNamespace,ModuleType
from unittest.mock import patch

from manipulation_agent.contracts import SkillError


@unittest.skipUnless(importlib.util.find_spec('torch'),'Pinned simulator Python required')
class ArticulationTests(unittest.TestCase):
    def preview(self,fail=False):
        import numpy as np
        import torch
        from manipulation_agent.executors.local_articulation import prepare_articulation
        q=[torch.zeros(1)];samples=[];records=[];Open=object()
        class Link:
            is_meta_link=False;prim_path='/door/leaf'
            @property
            def collision_boundary_points_world(self):
                return torch.tensor([[x+float(q[0][0]),y,z] for x in (0.,.05) for y in (0.,1.) for z in (.1,1.)])
        def set_joints(value):
            samples.append(float(value[0]));q[0]=value.clone()
        obj=SimpleNamespace(prim_path='/door',links={'leaf':Link()},
            get_joint_positions=lambda:q[0].clone(),set_joint_positions=set_joints)
        def setter(value,fully):
            random.random();q[0]=torch.ones(1) if value else torch.zeros(1);return True
        obj.states={Open:SimpleNamespace(set_value=setter)}
        loads=[]
        def load(state,**kw):
            loads.append(state);q[0]=state.clone()
        scene=SimpleNamespace(objects=[obj])
        sim=SimpleNamespace(dump_state=lambda **kw:q[0].clone(),load_state=load,scenes=[scene])
        backend=SimpleNamespace(og=SimpleNamespace(sim=sim),torch=torch,steps=0,frames_revision=0,
            _container_payload=lambda obj:[],_relocate_container_payload=lambda p:None,
            _anchored_operation=lambda obj:nullcontext(),_editable_carry_joints=lambda obj:nullcontext(),
            _carry_follow=lambda:None,_local_pose=lambda:(3.,0.,0.),_get_held=lambda:None,
            robot=SimpleNamespace(links={}),_navigation_layers='original')
        backend._placement_physics_step=lambda:self.fail('Hypothetical door must not push surrounding bodies')
        cache={'free':np.zeros((2,2),dtype='uint8'),'joint_signature':0.}
        backend._live_gt_collision_cache=cache
        builds=[]
        def geometry(op,*,occupancy_overrides=None):
            backend._navigation_layers='sample:'+str(float(q[0][0]))
            builds.append(float(q[0][0]))
            if len(builds)==2:
                self.assertEqual(set(occupancy_overrides),{'/door/leaf'})
                self.assertAlmostEqual(float(occupancy_overrides['/door/leaf'][:,0].min()),1.)
            backend._live_gt_collision_cache['joint_signature']=float(q[0][0])
            backend._live_gt_collision_cache['free'][:]=int(q[0][0])
            if fail=='geometry' and len(builds)==2:
                raise SkillError('local_adjustment_exhausted','cancelled after hypothetical raster update')
            return (None,SimpleNamespace(motion_free=lambda a,b:True),None,None,None)
        backend._local_geometry=geometry
        def check():
            if fail is True and len(samples)==2:raise SkillError('local_adjustment_exhausted','probe limit')
        operation=SimpleNamespace(check=check,record=lambda *args,**kw:records.append((args,kw)))
        module=ModuleType('omnigibson.object_states');module.Open=Open
        rng=random.getstate()
        def choose_goal(*args):
            if fail=='identity':
                scene.objects=[]
                raise SkillError('target_changed','Endpoint physics replaced an object',changed=True)
            return torch.ones(1)
        with patch.dict(sys.modules,{'omnigibson':ModuleType('omnigibson'),'omnigibson.object_states':module}),\
             patch('manipulation_agent.executors.particle_payload.snapshot_particle_state',return_value=[]),\
             patch('manipulation_agent.executors.particle_payload.restore_particle_state'),\
             patch('manipulation_agent.executors.articulation_goal.checked_open_goal',side_effect=choose_goal):
            if fail:
                with self.assertRaises(SkillError):prepare_articulation(backend,obj,'open',operation)
            else:
                swept,future,plan=prepare_articulation(backend,obj,'open',operation)
                self.assertEqual(plan['goal_joints'],[1.]);self.assertTrue(swept.boxes)
        self.assertEqual(q[0].tolist(),[0.]);self.assertEqual(random.getstate(),rng)
        self.assertEqual(backend.steps,0);self.assertEqual(backend.frames_revision,-1)
        self.assertEqual(backend._navigation_layers,'sample:0.0')
        self.assertIs(backend._live_gt_collision_cache,cache)
        self.assertEqual(cache['joint_signature'],0.)
        self.assertFalse(cache['free'].any())
        if fail=='identity':
            self.assertEqual(loads,[])
            self.assertEqual(scene.objects,[])
        return samples

    def test_preview_restores_joints_rng_and_current_geometry(self):
        self.assertGreater(len(self.preview()),1)

    def test_failed_preview_still_restores_scene(self):
        self.assertEqual(len(self.preview(fail=True)),2)
        self.assertEqual(self.preview(fail='identity'),[])

    def test_cancellation_after_hypothetical_raster_update_restores_live_cache(self):
        self.assertGreater(len(self.preview(fail='geometry')),2)

    def test_door_sweep_blocks_stance_even_when_endpoints_clear(self):
        import numpy as np
        from manipulation_agent.executors.local_articulation import ArticulationSweep,swept_link_bounds
        before={'door':np.array([[0.,0.,0.],[.1,1.,1.]])}
        after={'door':np.array([[1.,0.,0.],[1.1,1.,1.]])}
        boxes=swept_link_bounds(before,after)
        checker=ArticulationSweep(SimpleNamespace(_local_pose=lambda:(0.,0.,0.),_get_held=lambda:None,
            robot=SimpleNamespace(links={})),boxes,SimpleNamespace(motion_free=lambda a,b:True))
        checker.bodies=[np.array([[.4,.4,.2],[.6,.6,.8]])]
        self.assertFalse(checker.motion_free((0.,0.,0.),(0.,0.,0.)))
        self.assertTrue(checker.motion_free((4.,0.,0.),(4.,0.,0.)))


if __name__=='__main__':unittest.main()
