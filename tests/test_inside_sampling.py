"""Assembly placement geometry, collision recovery and transaction behavior."""
import importlib.util
import math
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from manipulation_agent.executors.inside_sampling import assembly_orientations, inset_centers, sample_assembly_inside, envelope_protrusion
from manipulation_agent.executors.placement import inside_sample_with_fallback
from manipulation_agent.contracts import SkillError


class SamplerBudgetRecoveryTests(unittest.TestCase):
    def test_native_exhaustion_restores_before_fallback(self):
        calls=[]
        def native():
            calls.append('native')
            raise SkillError('native_sampling_budget_exhausted','fixture budget')
        result=inside_sample_with_fallback(native,lambda:calls.append('fallback') or True,
                                          lambda:calls.append('restore'),lambda record:None)
        self.assertEqual(result,(True,True))
        self.assertEqual(calls,['native','restore','fallback'])

    def test_external_deadline_never_starts_fallback(self):
        calls=[]
        def native():raise SkillError('execution_timeout','episode deadline')
        with self.assertRaises(SkillError):
            inside_sample_with_fallback(native,lambda:calls.append('fallback'),lambda:calls.append('restore'),lambda r:None)
        self.assertEqual(calls,[])

    def test_scene_change_during_native_search_prevents_fallback(self):
        calls=[]
        def restore():raise SkillError('target_changed','scene identity changed')
        with self.assertRaises(SkillError):
            inside_sample_with_fallback(lambda:False,lambda:calls.append('fallback'),restore,lambda r:None)
        self.assertEqual(calls,[])


class InsetCentersTests(unittest.TestCase):
    def test_recorded_box_rim_loses_to_internal_support(self):
        low=(-14.,9.,.65);high=(-13.4,9.7,.90);extent=(.5,.16,.095)
        self.assertGreater(envelope_protrusion((-13.7,9.35,.908),extent,low,high,.025),
                           envelope_protrusion((-13.7,9.35,.725),extent,low,high,.025))

    def test_every_center_keeps_whole_extent_inside(self):
        points = inset_centers((1., 2., 3.), (2., 4., 6.), (.3, .8, 1.2))
        self.assertEqual(len(points), 27)
        for p in points:
            for value, low, high, extent in zip(p, (1,2,3), (2,4,6), (.3,.8,1.2)):
                self.assertGreater(value-extent/2, low)
                self.assertLess(value+extent/2, high)

    def test_oversized_assembly_has_no_spurious_candidates(self):
        self.assertEqual(inset_centers((0,0,0), (1,1,1), (1.1,.1,.1)), [])

    def test_recorded_tight_fits_reach_physical_validation(self):
        # Recorded placement_retest geometry: these fit the volume AABB but
        # previously lost every candidate to the 12mm Z / 2mm XY insets.
        cases = [
            ((.2910165786743164, .32209181785583496, .1131281852722168),
             (.16069293022155762, .16447266936302185, .1019972562789917)),
            ((.3109314441680908, .3201044797897339, .3919306993484497),
             (.27587199211120605, .3161287307739258, .010726094245910645)),
        ]
        for volume, extent in cases:
            with self.subTest(volume=volume, extent=extent):
                points = inset_centers((0., 0., 0.), volume, extent)
                self.assertTrue(points)
                for point in points:
                    for center, size, bound in zip(point, extent, volume):
                        self.assertGreaterEqual(center - size / 2, 0.)
                        self.assertLessEqual(center + size / 2, bound)

    def test_exact_fit_is_a_candidate_not_a_success_certificate(self):
        self.assertEqual(inset_centers((0., 0., 0.), (1., 1., 1.), (1., 1., 1.)),
                         [(.5, .5, .5)])


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
                # Match the installed official predicate: center in volume,
                # not the stricter whole-envelope rule under regression.
                self.states={Inside:SimpleNamespace(get_value=lambda target:any(
                    bool(l.check_points_in_volume(((self.aabb[0]+self.aabb[1])/2).unsqueeze(0)).item())
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
            check_points_in_volume=lambda points:((points >= bounds[0]) & (points <= bounds[1])).all(dim=1),
            get_position_orientation=target.get_position_orientation)
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
        states=ModuleType('omnigibson.object_states');states.Inside=Inside;states.Open=type('Open',(),{})
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

    def test_rotated_destination_adds_aligned_yaw_without_tipping(self):
        b,held,_,_,_,_,modules=self.fixture()
        transforms=modules['omnigibson.utils'].transform_utils
        carried=transforms.euler2quat(b.torch.tensor([0.,0.,.2]))
        destination=transforms.euler2quat(b.torch.tensor([0.,0.,.7]))
        candidates=assembly_orientations(held,carried,b.torch,transforms,reference_quat=destination)
        self.assertTrue(any(abs(float(b.torch.dot(q,destination)))>.99999 for _,q,_ in candidates))
        for _,quat,_ in candidates:
            self.assertTrue(b.torch.allclose(transforms.quat2mat(quat)[2],transforms.quat2mat(carried)[2],atol=1e-6))

    def test_occupied_center_searches_alternative_and_preserves_payload(self):
        b,held,target,contents,state,records,modules=self.fixture()
        with patch.dict('sys.modules',modules):
            self.assertTrue(sample_assembly_inside(b,held,target,contents))
        self.assertTrue(any(r.get('stage')=='initial_collision' for r in records))
        self.assertTrue(records[-1]['accepted'])
        self.assertGreater(state['loads'],0)
        self.assertGreater(abs(float(held.position[1])),.1)

    def test_unladen_item_chooses_bottom_before_native_inside_rim_false_fit(self):
        b,held,target,_,state,records,modules=self.fixture(collide=False)
        # Both centers satisfy the unchanged native predicate. The first
        # support leaves half the rigid item above the annotated interior.
        b._inside_support_centers=lambda *a,**kw:[(0.,0.,1.025),(0.,0.,.225)]
        with patch.dict('sys.modules',modules):
            self.assertTrue(sample_assembly_inside(b,held,target,[],max_candidates=1))
        self.assertAlmostEqual(float((held.position+held.offset)[2]),.225,places=5)
        self.assertEqual(records[-1]['attempted'],1)

    def test_interior_rank_does_not_waive_collision(self):
        b,held,target,_,state,records,modules=self.fixture(collide='all')
        original=held.get_position_orientation()
        b._inside_support_centers=lambda *a,**kw:[(0.,0.,1.025),(0.,0.,.225)]
        with patch.dict('sys.modules',modules):
            self.assertFalse(sample_assembly_inside(b,held,target,[],max_candidates=1))
        self.assertTrue(b.torch.equal(held.position,original[0]))
        self.assertEqual(records[-1]['rejections'],{'initial_collision':1})

    def test_small_budget_reaches_empty_space_despite_many_yaws(self):
        b,held,target,contents,state,records,modules=self.fixture()
        with patch.dict('sys.modules',modules):
            self.assertTrue(sample_assembly_inside(b,held,target,contents,max_candidates=4))
        self.assertEqual(records[-1]['attempted'],4)
        self.assertEqual(records[-1]['rejections']['initial_collision'],3)

    def test_low_volume_bound_collision_does_not_starve_higher_candidates(self):
        b,held,target,contents,state,records,modules=self.fixture()
        # A fillable volume can extend into the container's thick bottom.
        # A small work budget must still reach the physically usable height.
        modules['omnigibson.utils.usd_utils'].RigidContactAPI.is_in_contact=lambda **kw: (
            float((held.position+held.offset)[2])<.3 if state['steps']==1 else True)
        with patch.dict('sys.modules',modules):
            self.assertTrue(sample_assembly_inside(b,held,target,contents,max_candidates=5))
        self.assertLessEqual(records[-1]['attempted'],5)
        self.assertGreater(float((held.position+held.offset)[2]),.3)

    def test_passive_lid_motion_does_not_reject_stationary_open_interior(self):
        b,held,target,contents,state,records,modules=self.fixture(collide=False)
        target.n_joints=1
        target.get_joint_positions=lambda:b.torch.tensor([state['steps']*.1])
        target.get_joint_dof_types=lambda:b.torch.tensor([True])
        target.states[modules['omnigibson.object_states'].Open]=SimpleNamespace(get_value=lambda:True)
        with patch.dict('sys.modules',modules):
            self.assertTrue(sample_assembly_inside(b,held,target,contents,max_candidates=1))

    def test_moving_drawer_volume_or_closing_container_is_rejected(self):
        for moving_drawer in (True,False):
            with self.subTest(moving_drawer=moving_drawer):
                b,held,target,contents,state,records,modules=self.fixture(collide=False)
                if moving_drawer:
                    target.links['volume'].get_position_orientation=lambda:(
                        target.position+b.torch.tensor([.1 if state['steps'] else 0.,0.,0.]),target.quat)
                else:
                    target.states[modules['omnigibson.object_states'].Open]=SimpleNamespace(get_value=lambda:state['steps']==0)
                with patch.dict('sys.modules',modules):
                    self.assertFalse(sample_assembly_inside(b,held,target,contents,max_candidates=1))
                reason='container_volume_moved' if moving_drawer else 'container_closed_during_sampling'
                self.assertEqual(records[-1]['rejections'][reason],1)

    def test_all_collisions_restore_original_pose(self):
        b,held,target,contents,state,records,modules=self.fixture(collide='all')
        original=held.get_position_orientation()
        with patch.dict('sys.modules',modules):
            self.assertFalse(sample_assembly_inside(b,held,target,contents,max_candidates=3))
        self.assertTrue(b.torch.equal(held.position,original[0]))
        self.assertEqual(records[-1]['rejections']['initial_collision'],3)

    def test_recorded_plate_larger_than_annotation_reaches_physics(self):
        b,held,target,contents,state,records,modules=self.fixture(collide=False)
        held.extent=b.torch.tensor([.40778923,.40779799,.04712933])
        volume=target.links['volume']
        low=b.torch.zeros(3);high=b.torch.tensor([.90788984,.34610021,1.23199081])
        volume.visual_aabb=(low,high)
        volume.check_points_in_volume=lambda points:((points>=low)&(points<=high)).all(dim=1)
        with patch.dict('sys.modules',modules):
            self.assertTrue(sample_assembly_inside(b,held,target,contents,max_candidates=3))
        self.assertGreater(state['steps'],0)
        self.assertEqual(records[-1]['domain_attempts'],{'full_volume':1})
        self.assertEqual(records[-2]['center_domain'],'full_volume')
        self.assertTrue(b.torch.allclose(contents[0][0].position,held.position+held.offset))

    def test_full_volume_fallback_still_rejects_collisions_and_restores_pose(self):
        b,held,target,contents,state,records,modules=self.fixture(oversize=True,collide='all')
        original=held.get_position_orientation()
        with patch.dict('sys.modules',modules):
            self.assertFalse(sample_assembly_inside(b,held,target,contents,max_candidates=3))
        self.assertEqual(state['steps'],0)
        self.assertGreater(state['loads'],0)
        self.assertEqual(records[-1]['attempted'],3)
        self.assertEqual(records[-1]['rejections'],{'initial_collision':3})
        self.assertTrue(b.torch.equal(held.position,original[0]))

    def test_full_volume_fallback_does_not_bypass_payload_inside(self):
        b,held,target,contents,state,records,modules=self.fixture(oversize=True,collide=False)
        child=contents[0][0]
        child.states[modules['omnigibson.object_states'].Inside].get_value=lambda target:False
        with patch.dict('sys.modules',modules):
            self.assertFalse(sample_assembly_inside(b,held,target,contents,max_candidates=1))
        self.assertEqual(records[-1]['rejections'],{'payload_outside_destination':1})
        self.assertEqual(float(held.position[0]),5.)

    def test_carrier_fits_but_captured_particle_extent_is_too_large(self):
        b,held,target,contents,state,records,modules=self.fixture(collide=False)
        b.env=SimpleNamespace(scene=object())
        b._placement_particle_payload=(held,[object()])
        def oversized(*args):
            return [(held.position-b.torch.tensor([.6,.1,.1]),
                     held.position+b.torch.tensor([.6,.1,.1]))]
        with patch.dict('sys.modules',modules), \
             patch('manipulation_agent.executors.particle_payload.particle_bounds', side_effect=oversized), \
             patch('manipulation_agent.executors.particle_payload.particles_in_volumes', return_value=False) as check:
            self.assertFalse(sample_assembly_inside(b,held,target,contents,max_candidates=1))
        check.assert_called_once()
        self.assertEqual(state['steps'],0)
        self.assertEqual(records[-1]['attempted'],1)
        self.assertEqual(records[-1]['domain_attempts'],{'full_volume':1})
        self.assertIn('particles_outside_destination',records[-1]['rejections'])
        self.assertEqual(float(held.position[0]),5.)

    def test_destination_and_actual_particle_support_are_both_required(self):
        for outside in (True,False):
            with self.subTest(outside=outside):
                b,held,target,contents,state,records,modules=self.fixture(collide=False)
                b.env=SimpleNamespace(scene=object())
                b._placement_particle_payload=(held,[object()])
                with patch.dict('sys.modules',modules), \
                     patch('manipulation_agent.executors.particle_payload.particle_bounds',return_value=[]), \
                     patch('manipulation_agent.executors.particle_payload.particles_in_volumes',return_value=not outside), \
                     patch('manipulation_agent.executors.particle_payload.verify_particles',
                           side_effect=SkillError('postcondition_error','foreign support')) as check:
                    self.assertFalse(sample_assembly_inside(b,held,target,contents,max_candidates=1))
                self.assertEqual(float(held.position[0]),5.)
                reason='particles_outside_destination' if outside else 'particle_payload_postcondition'
                self.assertEqual(records[-1]['rejections'][reason],1)
                self.assertEqual(check.call_count,0 if outside else 1)

    def test_particle_identity_change_aborts_search_instead_of_trying_another_pose(self):
        b,held,target,contents,state,records,modules=self.fixture(collide=False)
        b.env=SimpleNamespace(scene=object());b._placement_particle_payload=(held,[object()])
        with patch.dict('sys.modules',modules), \
             patch('manipulation_agent.executors.particle_payload.particle_bounds',return_value=[]), \
             patch('manipulation_agent.executors.particle_payload.particles_in_volumes',return_value=True), \
             patch('manipulation_agent.executors.particle_payload.verify_particles',
                   side_effect=SkillError('target_changed','cooking replaced captured particle')):
            with self.assertRaises(SkillError) as error:
                sample_assembly_inside(b,held,target,contents,max_candidates=10)
        self.assertEqual(error.exception.code,'target_changed')
        self.assertEqual(records[-1]['attempted'],1)
        self.assertEqual(state['loads'],0)

    def test_rejected_candidate_cannot_restore_particles_replaced_by_physics(self):
        b,held,target,contents,state,records,modules=self.fixture(collide='all')
        with patch.dict('sys.modules',modules), \
             patch('manipulation_agent.executors.particle_payload.particle_state_unchanged',return_value=False):
            with self.assertRaises(SkillError) as error:
                sample_assembly_inside(b,held,target,contents,max_candidates=2)
        self.assertEqual(error.exception.code,'target_changed')
        self.assertEqual(state['loads'],0)
        self.assertTrue(records[-1]['identity_changed'])


if __name__=='__main__':unittest.main()
