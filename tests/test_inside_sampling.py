"""Assembly placement geometry, collision recovery and transaction behavior."""
import importlib.util
import itertools
import math
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from manipulation_agent.executors.inside_sampling import assembly_orientations, inset_centers, sample_assembly_inside, envelope_protrusion
from manipulation_agent.contracts import SkillError


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
            _placement_record=records.append,_placement_contacts=lambda *args:['fixture_obstacle'],
            _placement_contact_report=lambda *args, **kwargs:{'admissible':False,'reason':'deep_penetration'})
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

    def test_single_release_keeps_unladen_drop_above_opening_without_simulating(self):
        from manipulation_agent.executors.inside_sampling import select_inside_release
        for loaded in (False, True):
            with self.subTest(loaded=loaded):
                b, held, target, contents, state, _, modules = self.fixture()
                torch = b.torch
                corners = lambda bounds: torch.tensor(list(itertools.product(*zip(*[v.tolist() for v in bounds]))))
                volume = target.links['volume']
                volume.visual_boundary_points_world = corners(volume.visual_aabb)
                b.env = SimpleNamespace(scene=SimpleNamespace())
                b._collision_vertices = lambda obj: corners(obj.aabb)
                b._inside_support_centers = lambda *args, **kwargs: [(0., 0., 1.125)]
                b._inside_pose_overlap_ranker = lambda *args: lambda position, quat: int(float((position + held.offset)[2]) < 1.)
                b._inside_overlap_ranker = lambda *args: lambda *args: 0
                before = held.get_position_orientation()
                with patch.dict('sys.modules', modules), patch(
                    'manipulation_agent.executors.inside_sampling.assembly_orientations',
                    return_value=[('carried_pose', held.quat, 0.)]):
                    candidate = select_inside_release(b, held, target, contents if loaded else [])
                self.assertEqual(float((candidate['pose'][0] + held.offset)[2]) > 1., not loaded)
                self.assertEqual(state, {'steps': 0, 'loads': 0})
                self.assertTrue(all(torch.equal(a, z) for a, z in zip(before, held.get_position_orientation())))

    def test_single_release_prefers_closed_door_clear_pose_over_nearer_doorway_point(self):
        import numpy as np
        from manipulation_agent.executors.inside_sampling import select_inside_release
        b, held, target, _, state, _, modules = self.fixture()
        torch = b.torch
        corners = lambda bounds: torch.tensor(list(itertools.product(*zip(*[v.tolist() for v in bounds]))))
        volume = target.links['volume']
        volume.visual_boundary_points_world = corners(volume.visual_aabb)
        b.env = SimpleNamespace(scene=SimpleNamespace())
        b._collision_vertices = lambda obj: corners(obj.aabb)
        # Support exists both near the front doorway (x=0.25, overlapping the
        # closed door plug x=[0.15, 0.45]) and deeper inside the drum (x=-0.20).
        b._inside_support_centers = lambda *args, **kwargs: [(.25, 0., .3), (-.20, 0., .3)]
        b._inside_pose_overlap_ranker = lambda *args: lambda position, quat: 0
        b._inside_overlap_ranker = lambda *args: lambda *args: 0
        geometry = ({'volume': [(np.array([.15, -.5, 0.]), np.array([.45, .5, 1.]))]},
                    {'volume': (np.eye(3), np.zeros(3))})
        doorway_click = torch.tensor([.45, 0., .5])
        with patch.dict('sys.modules', modules), \
             patch('manipulation_agent.executors.inside_sampling.assembly_orientations',
                   return_value=[('carried_pose', held.quat, 0.)]), \
             patch('manipulation_agent.executors.closure_clearance.closed_geometry_proposals',
                   return_value=geometry):
            candidate = select_inside_release(b, held, target, [], point=doorway_click)
        chosen_center = candidate['pose'][0] + held.offset
        self.assertAlmostEqual(float(chosen_center[0]), -.20, places=4)
        self.assertEqual(state, {'steps': 0, 'loads': 0})

    def test_single_release_prefers_inset_resting_pose_over_lip_edge_standing_proposal(self):
        from manipulation_agent.executors.inside_sampling import select_inside_release
        b, held, target, _, state, _, modules = self.fixture()
        torch = b.torch
        held.extent = torch.tensor([.20, .12, .06])
        held.offset = torch.zeros(3)
        corners = lambda bounds: torch.tensor(list(itertools.product(*zip(*[v.tolist() for v in bounds]))))
        volume = target.links['volume']
        volume.visual_aabb = (torch.tensor([0., -.40, 1.20]), torch.tensor([.40, .40, 1.50]))
        volume.visual_boundary_points_world = corners(volume.visual_aabb)
        volume.check_points_in_volume = lambda points: (
            (points >= volume.visual_aabb[0]) & (points <= volume.visual_aabb[1])
        ).all(dim=1)
        b.env = SimpleNamespace(scene=SimpleNamespace())
        b._collision_vertices = lambda obj: corners(obj.aabb)
        shelf_z = 1.225
        # Mirror _inside_support_centers: inset domain keeps x >= 0.10, while
        # full_volume adds x = 0.06 overhanging the front shelf lip toward the
        # exterior cabinet frame click at x = -0.08, z = 1.43.
        def support_centers(_h, _t, _v, centers, offset, *_a, bottom_offset=None, **_kw):
            half_z = float(offset[2] - bottom_offset[2])
            return [(float(c[0]), float(c[1]), shelf_z + half_z + .025) for c in centers]
        b._inside_support_centers = support_centers
        b._inside_pose_overlap_ranker = lambda *args: lambda position, quat: 0
        b._inside_overlap_ranker = lambda *args: lambda *args: 0
        flat_quat = torch.tensor([0., 0., 0., 1.])
        stand_quat = torch.tensor([0., math.sqrt(.5), 0., math.sqrt(.5)])
        frame_click = torch.tensor([-.08, .20, 1.43])
        with patch.dict('sys.modules', modules), \
             patch('manipulation_agent.executors.inside_sampling.assembly_orientations',
                   return_value=[('asset_resting_pose', flat_quat, 0.),
                                 ('unladen_face_proposal', stand_quat, 0.)]), \
             patch('manipulation_agent.executors.closure_clearance.closed_geometry_proposals',
                   return_value=None):
            candidate = select_inside_release(b, held, target, [], point=frame_click)
        self.assertEqual(candidate['domain'], 'support_inset')
        self.assertEqual(candidate['orientation_source'], 'asset_resting_pose')
        self.assertGreaterEqual(float(candidate['pose'][0][0]), .10 - 1e-5)
        self.assertEqual(state, {'steps': 0, 'loads': 0})

    def test_single_release_falls_back_to_face_proposal_when_resting_pose_collides(self):
        from manipulation_agent.executors.inside_sampling import select_inside_release
        b, held, target, _, _, _, modules = self.fixture()
        torch = b.torch
        held.extent = torch.tensor([.20, .12, .06])
        held.offset = torch.zeros(3)
        corners = lambda bounds: torch.tensor(list(itertools.product(*zip(*[v.tolist() for v in bounds]))))
        volume = target.links['volume']
        volume.visual_boundary_points_world = corners(volume.visual_aabb)
        b.env = SimpleNamespace(scene=SimpleNamespace())
        b._collision_vertices = lambda obj: corners(obj.aabb)
        b._inside_support_centers = lambda _h, _t, _v, centers, *_a, **_kw: [
            (float(c[0]), float(c[1]), .20) for c in centers[:2]
        ]
        flat_quat = torch.tensor([0., 0., 0., 1.])
        stand_quat = torch.tensor([0., math.sqrt(.5), 0., math.sqrt(.5)])
        b._inside_pose_overlap_ranker = lambda *args: (
            lambda position, quat: int(float(torch.abs(torch.dot(quat, flat_quat))) > .99)
        )
        b._inside_overlap_ranker = lambda *args: lambda *args: 0
        with patch.dict('sys.modules', modules), \
             patch('manipulation_agent.executors.inside_sampling.assembly_orientations',
                   return_value=[('asset_resting_pose', flat_quat, 0.),
                                 ('unladen_face_proposal', stand_quat, 0.)]), \
             patch('manipulation_agent.executors.closure_clearance.closed_geometry_proposals',
                   return_value=None):
            candidate = select_inside_release(b, held, target, [])
        self.assertEqual(candidate['orientation_source'], 'unladen_face_proposal')
        self.assertEqual(candidate['proposal_overlap_score'], 0)

    def test_inside_support_centers_admits_flush_drawer_floor_and_rejects_lower_compartment(self):
        from manipulation_agent.executors.placement import CheckedPlacement
        b, held, target, _, _, records, modules = self.fixture()
        torch = b.torch
        placement = CheckedPlacement()
        placement.torch = torch
        placement.og = b.og
        placement.robot = SimpleNamespace(links={'base': SimpleNamespace(prim_path='/robot')})
        placement._placement_record = records.append
        held.links = {'base': SimpleNamespace(prim_path='/held', is_meta_link=False)}
        drawer_link = SimpleNamespace(prim_path='/drawer/right', is_meta_link=False)
        volume = SimpleNamespace(
            prim_path='/drawer/meta_fillable', is_meta_link=True, meta_link_type='fillable',
            visual_aabb=(torch.tensor([-.2, -.3, .250]), torch.tensor([.2, .3, .440])),
        )
        target.links = {'right': drawer_link, 'vol': volume}
        sampling = ModuleType('omnigibson.utils.sampling_utils')
        # Hit 1: drawer bottom slab 3 mm below visual_aabb low[2] (0.247 vs 0.250).
        # Hit 2: lower shelf 20 cm below visual_aabb low[2] (0.050).
        sampling.raytest = lambda *_a, **_kw: [
            {'rigidBody': '/drawer/right', 'normal': torch.tensor([0., 0., 1.]),
             'position': torch.tensor([0., 0., .247]), 'collision': '/drawer/right/floor'},
            {'rigidBody': '/drawer/right', 'normal': torch.tensor([0., 0., 1.]),
             'position': torch.tensor([0., 0., .050]), 'collision': '/drawer/right/lower'},
        ]
        with patch.dict('sys.modules', {**modules, 'omnigibson.utils.sampling_utils': sampling}):
            # Normal 8 cm tall pencil case: resting_z = 0.247 + 0.040 = 0.287 >= 0.250 -> admitted;
            # lower compartment hit at 0.050 < 0.250 - 0.015 -> rejected.
            admitted = placement._inside_support_centers(
                held, target, volume, [(0., 0., .30)], torch.zeros(3), [],
                lambda: None, bottom_offset=torch.tensor([0., 0., -.04]),
            )
            # Paper-thin 2 mm item on 0.247 floor: resting_z = 0.248 < 0.250 -> rejected.
            too_low = placement._inside_support_centers(
                held, target, volume, [(0., 0., .30)], torch.zeros(3), [],
                lambda: None, bottom_offset=torch.tensor([0., 0., -.001]),
            )
        expected_z = .247 + .040 + .025 + .5 * 9.81 * (1 / 120) ** 2
        self.assertEqual(len(admitted), 9)
        for _, _, z in admitted:
            self.assertAlmostEqual(z, expected_z, places=6)
        self.assertEqual(too_low, [])

    def test_single_release_chooses_flush_floor_support_over_midair_inset_drop(self):
        from manipulation_agent.executors.inside_sampling import select_inside_release
        b, held, target, _, state, _, modules = self.fixture()
        torch = b.torch
        held.extent = torch.tensor([.20, .08, .08])
        held.offset = torch.zeros(3)
        corners = lambda bounds: torch.tensor(list(itertools.product(*zip(*[v.tolist() for v in bounds]))))
        volume = target.links['volume']
        volume.visual_aabb = (torch.tensor([-.20, -.30, .250]), torch.tensor([.20, .30, .440]))
        volume.visual_boundary_points_world = corners(volume.visual_aabb)
        volume.check_points_in_volume = lambda points: (
            (points >= volume.visual_aabb[0]) & (points <= volume.visual_aabb[1])
        ).all(dim=1)
        b.env = SimpleNamespace(scene=SimpleNamespace())
        b._collision_vertices = lambda obj: corners(obj.aabb)
        clearance = .025 + .5 * 9.81 * b.og.sim.get_physics_dt() ** 2
        floor_z = .247  # 3 mm below fillable visual_aabb low[2] = 0.250
        b._inside_support_centers = lambda _h, _t, _v, centers, *_a, **_kw: [
            (float(c[0]), float(c[1]), floor_z + .04 + clearance) for c in centers
        ]
        b._inside_pose_overlap_ranker = lambda *args: lambda position, quat: 0
        b._inside_overlap_ranker = lambda *args: lambda *args: 0
        with patch.dict('sys.modules', modules), \
             patch('manipulation_agent.executors.inside_sampling.assembly_orientations',
                   return_value=[('asset_resting_pose', held.quat, 0.)]), \
             patch('manipulation_agent.executors.closure_clearance.closed_geometry_proposals',
                   return_value=None):
            candidate = select_inside_release(b, held, target, [], point=torch.tensor([0., 0., .38]))
        self.assertEqual(candidate['domain'], 'support_inset')
        self.assertAlmostEqual(float(candidate['pose'][0][2]), floor_z + .04 + clearance, places=6)
        self.assertEqual(state, {'steps': 0, 'loads': 0})

    def test_single_release_downgrades_out_of_inset_click_projection_to_support_full_volume(self):
        from manipulation_agent.executors.inside_sampling import select_inside_release
        b, held, target, _, state, _, modules = self.fixture()
        torch = b.torch
        held.extent = torch.tensor([.20, .12, .06])
        held.offset = torch.zeros(3)
        yaw90 = torch.tensor([0., 0., math.sqrt(.5), math.sqrt(.5)])
        target.quat = yaw90
        corners = lambda bounds: torch.tensor(list(itertools.product(*zip(*[v.tolist() for v in bounds]))))
        volume = target.links['volume']
        # Rotated 90 deg around Z: world Y in [0.0, 0.40] is container depth (lip at y=0.0).
        volume.visual_aabb = (torch.tensor([-.40, 0., 1.20]), torch.tensor([.40, .40, 1.50]))
        volume.visual_boundary_points_world = corners(volume.visual_aabb)
        volume.check_points_in_volume = lambda points: (
            (points >= volume.visual_aabb[0]) & (points <= volume.visual_aabb[1])
        ).all(dim=1)
        b.env = SimpleNamespace(scene=SimpleNamespace())
        b._collision_vertices = lambda obj: corners(obj.aabb)
        clearance = .025 + .5 * 9.81 * b.og.sim.get_physics_dt() ** 2
        # Simulate _inside_support_centers injecting a lip-projected click (y=0.04,
        # overhanging the y=0.0 lip for a 0.20 m deep item) ahead of interior centers.
        b._inside_support_centers = lambda _h, _t, _v, centers, *_a, **_kw: [
            (0., .04, 1.22 + .03 + clearance),
            *[(float(c[0]), float(c[1]), 1.22 + .03 + clearance) for c in centers],
        ]
        b._inside_pose_overlap_ranker = lambda *args: lambda position, quat: 0
        b._inside_overlap_ranker = lambda *args: lambda *args: 0
        with patch.dict('sys.modules', modules), \
             patch('manipulation_agent.executors.inside_sampling.assembly_orientations',
                   return_value=[('asset_resting_pose', yaw90, 0.)]), \
             patch('manipulation_agent.executors.closure_clearance.closed_geometry_proposals',
                   return_value=None):
            candidate = select_inside_release(b, held, target, [], point=torch.tensor([0., -.05, 1.40]))
        self.assertEqual(candidate['domain'], 'support_inset')
        self.assertGreaterEqual(float(candidate['pose'][0][1]), .10 - 1e-4)
        self.assertEqual(state, {'steps': 0, 'loads': 0})

    def test_single_release_scores_closed_lid_against_settled_support_height(self):
        import numpy as np
        from manipulation_agent.executors.inside_sampling import select_inside_release
        b, held, target, _, _, _, modules = self.fixture()
        torch = b.torch
        held.extent = torch.tensor([.16, .16, .10])
        held.offset = torch.zeros(3)
        corners = lambda bounds: torch.tensor(list(itertools.product(*zip(*[v.tolist() for v in bounds]))))
        volume = target.links['volume']
        volume.visual_boundary_points_world = corners(volume.visual_aabb)
        b.env = SimpleNamespace(scene=SimpleNamespace())
        b._collision_vertices = lambda obj: corners(obj.aabb)
        clearance = .025 + .5 * 9.81 * b.og.sim.get_physics_dt() ** 2
        # Floor support settles center at z = 0.29 (top = 0.34 < lid at 0.35),
        # whereas pre-drop release center is z = 0.3153 (top = 0.3653 > 0.35).
        b._inside_support_centers = lambda *_a, **_kw: [(0., 0., .29 + clearance)]
        b._inside_pose_overlap_ranker = lambda *args: lambda position, quat: 0
        b._inside_overlap_ranker = lambda *args: lambda *args: 0
        geometry = ({'volume': [(np.array([-.5, -.5, .35]), np.array([.5, .5, .40]))]},
                    {'volume': (np.eye(3), np.zeros(3))})
        with patch.dict('sys.modules', modules), \
             patch('manipulation_agent.executors.inside_sampling.assembly_orientations',
                   return_value=[('asset_resting_pose', held.quat, 0.)]), \
             patch('manipulation_agent.executors.closure_clearance.closed_geometry_proposals',
                   return_value=geometry):
            candidate = select_inside_release(b, held, target, [])
        self.assertEqual(candidate['domain'], 'support_inset')
        self.assertAlmostEqual(float(candidate['pose'][0][2]), .29 + clearance, places=6)

    def test_closed_geometry_proposals_excludes_comoving_drawer_body_and_keeps_hinged_door(self):
        from manipulation_agent.executors.closure_clearance import (
            closed_geometry_overlap, closed_geometry_proposals,
        )
        b, _, _, _, _, _, modules = self.fixture()
        torch = b.torch
        Open = modules['omnigibson.object_states'].Open
        joint = {'closed': False}
        ident = torch.tensor([0., 0., 0., 1.])
        yaw90 = torch.tensor([0., 0., math.sqrt(.5), math.sqrt(.5)])
        corners = lambda lo, hi: torch.tensor(list(itertools.product(*zip(lo, hi))), dtype=torch.float32)
        drawer_tub = SimpleNamespace(
            prim_path='drawer_tub', is_meta_link=False,
            get_position_orientation=lambda: (
                torch.tensor([0. if joint['closed'] else .35, 0., .2]), ident),
            collision_boundary_points_world=corners([-.2, -.3, 0.], [.2, .3, .4]),
        )
        hinged_door = SimpleNamespace(
            prim_path='hinged_door', is_meta_link=False,
            get_position_orientation=lambda: (
                torch.tensor([.25, 0., .7]), ident if joint['closed'] else yaw90),
            collision_boundary_points_world=corners([.15, -.3, .45], [.30, .3, .95]),
        )
        drawer_vol = SimpleNamespace(
            prim_path='drawer_vol', is_meta_link=True, meta_link_type='fillable',
            get_position_orientation=lambda: (
                torch.tensor([0. if joint['closed'] else .35, 0., .2]), ident),
        )
        drum_vol = SimpleNamespace(
            prim_path='drum_vol', is_meta_link=True, meta_link_type='fillable',
            get_position_orientation=lambda: (torch.tensor([0., 0., .7]), ident),
        )

        def set_open(val, fully=False):
            joint['closed'] = not val
            return True

        def set_joints(j):
            joint['closed'] = float(j[0]) == 0.

        open_state = SimpleNamespace(
            get_value=lambda: not joint['closed'], set_value=set_open, clear_cache=lambda: None,
        )
        container = SimpleNamespace(
            states={Open: open_state},
            get_joint_positions=lambda: torch.tensor([0. if joint['closed'] else 1.]),
            set_joint_positions=set_joints,
            state_updated=lambda: None,
            links={'tub': drawer_tub, 'door': hinged_door, 'dvol': drawer_vol, 'bvol': drum_vol},
        )
        with patch.dict('sys.modules', modules):
            geometry = closed_geometry_proposals(b, container)
        self.assertFalse(joint['closed'])
        # Inside the sliding drawer, the co-moving drawer_tub is excluded while
        # the hinged_door (if overlapping closed x=[0.15,0.30]) is still scored.
        self.assertEqual(closed_geometry_overlap(geometry, drawer_vol, [.35, 0., .2], [.1, .1, .1]), 0.)
        self.assertGreater(closed_geometry_overlap(geometry, drum_vol, [.20, 0., .7], [.2, .2, .2]), 0.)
        self.assertEqual(closed_geometry_overlap(geometry, drum_vol, [-.10, 0., .7], [.2, .2, .2]), 0.)

    def test_loaded_placement_prefers_selected_shelf_after_diversification(self):
        # Both shelves pass the same native checks. The click, rather than
        # the broad volume's lowest support, selects which one to try first.
        for selected, expected in ((.8, .825), (.2, .225)):
            b,held,target,contents,_,records,modules=self.fixture(collide=False)
            b._inside_support_centers=lambda *a,**kw:[(0.,0.,.225),(0.,0.,.825)]
            b._inside_overlap_ranker=lambda assembly:lambda center,extent:0
            b._inside_pose_overlap_ranker=lambda held,assembly:lambda position,quat:0
            with patch.dict('sys.modules',modules):
                self.assertTrue(sample_assembly_inside(b,held,target,contents,
                    point=b.torch.tensor([0.,0.,selected])))
            accepted=next(r for r in records if r['status']=='inside_candidate_accepted')
            self.assertAlmostEqual(accepted['center'][2],expected)

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

    def test_flat_resting_metadata_does_not_exclude_container_supported_leaning(self):
        from scipy.spatial.transform import Rotation
        b,held,_,_,_,_,modules=self.fixture()
        transforms=modules['omnigibson.utils'].transform_utils
        transforms.euler2quat=lambda e:b.torch.tensor(Rotation.from_euler('xyz',e.numpy()).as_quat(),dtype=e.dtype)
        held.orientations={'flat':{'rotation':[0.,0.,0.,1.],'prob':1.}}
        carried=b.torch.tensor([0.,0.,0.,1.])
        candidates=assembly_orientations(held,carried,b.torch,transforms,allow_reorientation=True)
        self.assertTrue(any(abs(float(transforms.quat2mat(q)[2,2]))<.01 for _,q,_ in candidates))
        self.assertTrue(any(.4<abs(float(transforms.quat2mat(q)[2,2]))<.9 for _,q,_ in candidates))
        loaded=assembly_orientations(held,carried,b.torch,transforms,allow_reorientation=False)
        self.assertTrue(all(float(transforms.quat2mat(q)[2,2])>.99 for _,q,_ in loaded))

    def test_rotated_destination_adds_aligned_yaw_without_tipping(self):
        b,held,_,_,_,_,modules=self.fixture()
        transforms=modules['omnigibson.utils'].transform_utils
        carried=transforms.euler2quat(b.torch.tensor([0.,0.,.2]))
        destination=transforms.euler2quat(b.torch.tensor([0.,0.,.7]))
        candidates=assembly_orientations(held,carried,b.torch,transforms,reference_quat=destination)
        self.assertTrue(any(abs(float(b.torch.dot(q,destination)))>.99999 for _,q,_ in candidates))
        for _,quat,_ in candidates:
            self.assertTrue(b.torch.allclose(transforms.quat2mat(quat)[2],transforms.quat2mat(carried)[2],atol=1e-6))

    def test_empty_vessel_is_not_inverted_by_unladen_pose_search(self):
        b,held,_,_,_,_,modules=self.fixture()
        transforms=modules['omnigibson.utils'].transform_utils
        held.links={'volume':SimpleNamespace(is_meta_link=True,meta_link_type='fillable')}
        held.orientations={'inverted':{'rotation':[1.,0.,0.,0.],'prob':1.}}
        carried=b.torch.tensor([0.,0.,0.,1.])
        candidates=assembly_orientations(held,carried,b.torch,transforms,allow_reorientation=True)
        self.assertTrue(candidates)
        self.assertTrue(all(float(transforms.quat2mat(quat)[2,2])>.99 for _,quat,_ in candidates))

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

    def test_above_opening_release_requires_actual_settled_containment(self):
        for falls_inside in (True, False):
            with self.subTest(falls_inside=falls_inside):
                b,held,target,_,state,records,modules=self.fixture(collide=False)
                b._inside_support_centers=lambda *a,**kw:[(0.,0.,1.15)]
                inside=held.states[modules['omnigibson.object_states'].Inside]
                inside._get_value=inside.get_value
                b.og.sim.current_time_step_index=0
                released=[]
                def step():
                    state['steps']+=1
                    if state['steps']==1:
                        released.append(float((held.position+held.offset)[2]))
                    if falls_inside:
                        held.position[2]=max(float(held.position[2])-.1,.8-float(held.offset[2]))
                b.og.sim.step_physics=step
                with patch.dict('sys.modules',modules):
                    self.assertEqual(sample_assembly_inside(b,held,target,[],max_candidates=1),falls_inside)
                self.assertGreater(released[0],float(target.links['volume'].visual_aabb[1][2]))
                if not falls_inside:
                    self.assertEqual(records[-1]['rejections'],{'official_inside_false':1})

    def test_loaded_container_prioritizes_door_clear_pose_with_one_trial(self):
        import numpy as np
        from manipulation_agent.executors.closure_clearance import closed_geometry_overlap
        for collide in (False, 'all'):
            with self.subTest(collide=collide):
                b,held,target,contents,state,records,modules=self.fixture(collide=collide)
                target.states[modules['omnigibson.object_states'].Open]=SimpleNamespace(get_value=lambda:True)
                volume=target.links['volume']
                # Open geometry permits both positions; a closed door overlaps
                # the center but leaves room at the back of the same cavity.
                geometry=([(np.array([-.5,-.5,0.]),np.array([.1,.5,1.]))],
                          {'volume':(np.eye(3),np.zeros(3))})
                b._inside_overlap_ranker=lambda assembly:lambda center,extent:0.
                def closure(*args):
                    overlap=closed_geometry_overlap(geometry,volume,held.position+held.offset,held.extent)
                    return {'clear':not overlap}
                with patch.dict('sys.modules',modules), \
                     patch('manipulation_agent.executors.closure_clearance.closed_geometry_proposals',return_value=geometry), \
                     patch('manipulation_agent.executors.closure_clearance.check_closure_clearance',side_effect=closure):
                    result=sample_assembly_inside(b,held,target,contents,max_candidates=1)
                if collide:
                    self.assertFalse(result)
                    self.assertEqual(records[-1]['rejections'],{'initial_collision':1})
                else:
                    self.assertTrue(result)
                    self.assertEqual(closed_geometry_overlap(geometry,volume,held.position+held.offset,held.extent),0.)
                    self.assertFalse(any(r.get('closure_clearance')=='open_only_fallback' for r in records))
                self.assertEqual(records[-1]['attempted'],1)

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

    def test_conservative_overlap_keeps_supported_pose_ahead_of_low_colliding_volume(self):
        import numpy as np
        b,held,target,_,state,records,modules=self.fixture()
        target.states[modules['omnigibson.object_states'].Open]=SimpleNamespace(get_value=lambda:True)
        b._inside_support_centers=lambda *a,**kw:[(.2,.2,.525)]
        b._inside_overlap_ranker=lambda assembly:lambda center,extent:1
        b._inside_pose_overlap_ranker=lambda held,assembly:lambda position,quat:1
        modules['omnigibson.utils.usd_utils'].RigidContactAPI.is_in_contact=lambda **kw:(
            float((held.position+held.offset)[2])<.3 if state['steps']==1 else True)
        geometry=([],{'volume':(np.eye(3),np.zeros(3))})
        with patch.dict('sys.modules',modules), \
             patch('manipulation_agent.executors.closure_clearance.closed_geometry_proposals',return_value=geometry), \
             patch('manipulation_agent.executors.closure_clearance.check_closure_clearance',return_value={'clear':True}):
            self.assertTrue(sample_assembly_inside(b,held,target,[],max_candidates=1))
        self.assertEqual(records[-1]['attempted'],1)
        self.assertGreater(float((held.position+held.offset)[2]),.3)

    def test_loaded_assembly_reaches_clear_pose_beyond_first_overlap_window(self):
        b,held,target,contents,state,records,modules=self.fixture()
        # More than one ranking window of blocked shelf poses precedes the
        # usable compartment. Spend the one physics trial on that clear pose.
        b._inside_support_centers=lambda *a,**kw:[
            (0.,0.,.1+i*.001) for i in range(80)]+[(.2,.2,.525)]
        b._inside_overlap_ranker=lambda assembly:lambda center,extent:1
        b._inside_pose_overlap_ranker=lambda held,assembly:lambda position,quat:(
            int(float(b.torch.linalg.norm(b.torch.as_tensor(position)+held.offset-
                                         b.torch.tensor([.2,.2,.525])))>.01))
        with patch.dict('sys.modules',modules):
            self.assertTrue(sample_assembly_inside(b,held,target,contents,max_candidates=1))
        self.assertEqual(records[-1]['attempted'],1)
        self.assertGreater(records[-1]['overlap_ranking']['deferred'],64)
        self.assertTrue(b.torch.allclose(contents[0][0].position,held.position+held.offset))

    def test_passive_lid_motion_does_not_reject_stationary_open_interior(self):
        b,held,target,contents,state,records,modules=self.fixture(collide=False)
        target.n_joints=1
        target.get_joint_positions=lambda:b.torch.tensor([state['steps']*.1])
        target.get_joint_dof_types=lambda:b.torch.tensor([True])
        target.states[modules['omnigibson.object_states'].Open]=SimpleNamespace(get_value=lambda:True)
        with patch.dict('sys.modules',modules), \
             patch('manipulation_agent.executors.closure_clearance.closed_geometry_proposals',return_value=None), \
             patch('manipulation_agent.executors.closure_clearance.check_closure_clearance',return_value={'clear':True}):
            self.assertTrue(sample_assembly_inside(b,held,target,contents,max_candidates=1))
        self.assertTrue(b.torch.allclose(contents[0][0].position,held.position+held.offset))

    def test_moving_drawer_volume_or_closing_container_is_rejected(self):
        for moving_drawer in (True,False):
            with self.subTest(moving_drawer=moving_drawer):
                b,held,target,contents,state,records,modules=self.fixture(collide=False)
                if moving_drawer:
                    target.links['volume'].get_position_orientation=lambda:(
                        target.position+b.torch.tensor([.1 if state['steps'] else 0.,0.,0.]),target.quat)
                else:
                    target.states[modules['omnigibson.object_states'].Open]=SimpleNamespace(get_value=lambda:state['steps']==0)
                with patch.dict('sys.modules',modules), \
                     patch('manipulation_agent.executors.closure_clearance.closed_geometry_proposals',return_value=None):
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

    def test_missing_rigid_contact_requires_native_containment_and_stability(self):
        for stable in (True, False):
            with self.subTest(stable=stable):
                b, held, target, _, state, records, modules = self.fixture(collide=False)
                # Native rigid-body contact views exclude physical particles;
                # particle support must reach the existing resting check.
                modules['omnigibson.utils.usd_utils'].RigidContactAPI.is_in_contact = lambda **kw: False
                observed = []
                def resting(*args, **kwargs):
                    observed.append(state['steps'])
                    return stable
                b._inside_resting_check = resting
                with patch.dict('sys.modules', modules):
                    result = sample_assembly_inside(b, held, target, [], max_candidates=1)
                self.assertEqual(result, stable)
                self.assertTrue(observed)
                self.assertGreaterEqual(observed[0], 60)
                if not stable:
                    self.assertEqual(records[-1]['rejections']['unstable_after_release'], 1)

    def test_slow_physics_finishes_admitted_trial(self):
        b,held,target,contents,state,records,modules=self.fixture(collide=False)
        with patch.dict('sys.modules',modules), patch(
                'manipulation_agent.executors.inside_sampling.time.monotonic',
                side_effect=lambda:40. if state['steps'] else 0.):
            self.assertTrue(sample_assembly_inside(b,held,target,contents,max_seconds=30.))
        self.assertEqual(records[-1]['attempted'],1)
        self.assertTrue(records[-1]['accepted'])

    def test_shared_io_latency_does_not_stop_default_candidate_coverage(self):
        b,held,target,contents,state,records,modules=self.fixture()
        with patch.dict('sys.modules',modules), patch(
                'manipulation_agent.executors.inside_sampling.time.monotonic',
                side_effect=lambda:60. if any(r.get('stage')=='initial_collision' for r in records) else 0.):
            self.assertTrue(sample_assembly_inside(b,held,target,contents,max_candidates=4))
        self.assertEqual(records[-1]['attempted'],4)

    def test_explicit_admission_deadline_still_stops_additional_candidates(self):
        b,held,target,contents,state,records,modules=self.fixture()
        with patch.dict('sys.modules',modules), patch(
                'manipulation_agent.executors.inside_sampling.time.monotonic',
                side_effect=lambda:60. if any(r.get('stage')=='initial_collision' for r in records) else 0.), \
                self.assertRaises(SkillError) as caught:
            sample_assembly_inside(b,held,target,contents,max_candidates=4,max_seconds=30.)
        self.assertEqual(caught.exception.code,'sampling_budget_exhausted')
        self.assertEqual(records[-1]['attempted'],1)

    def test_geometry_preparation_cannot_consume_every_trial(self):
        b,held,target,contents,state,records,modules=self.fixture(collide=False)
        calls=iter([0.])
        with patch.dict('sys.modules',modules), patch(
                'manipulation_agent.executors.inside_sampling.time.monotonic',
                side_effect=lambda:next(calls,40.)):
            self.assertTrue(sample_assembly_inside(b,held,target,contents,max_seconds=30.))
        self.assertEqual(records[-1]['attempted'],1)

    def test_external_deadline_still_interrupts_admitted_trial(self):
        b,held,target,contents,state,records,modules=self.fixture(collide=False)
        def check(**kwargs):
            if state['steps']:raise SkillError('execution_timeout','cancelled')
        b.deadline.check=check
        with patch.dict('sys.modules',modules), self.assertRaises(SkillError) as caught:
            sample_assembly_inside(b,held,target,contents)
        self.assertEqual(caught.exception.code,'execution_timeout')
        self.assertEqual(float(held.position[0]),5.)

    def test_verified_native_inside_survives_optional_search_near_work_limit(self):
        b,held,target,contents,state,records,modules=self.fixture(collide='all')
        original=held.get_position_orientation()
        held.position=b.torch.tensor([0.,0.,.2])
        saved_particles=[object()]
        fallback={'state':b.og.sim.dump_state(), 'particles':saved_particles,
                  'candidate':{'candidate':0,'origin':'native_sampler'}}
        held.set_position_orientation(*original)
        b.steps=0;b.sampling_physics_steps=960
        b._local_operation=SimpleNamespace(start_sampling=0,start_steps=0,step_limit=600,
            clock=lambda:10.,started=0.,route_started=None,route_wall_seconds=0.,
            limits=SimpleNamespace(max_sampling_steps=1200,wall_seconds=300.))
        with patch.dict('sys.modules',modules), patch(
                'manipulation_agent.executors.particle_payload.restore_particle_state') as restore_particles:
            self.assertTrue(sample_assembly_inside(b,held,target,contents,open_fallback=fallback))
        restore_particles.assert_called_once_with(saved_particles)
        self.assertEqual(float(held.position[0]),0.)
        self.assertEqual(records[-1]['attempted'],0)
        self.assertTrue(records[-1]['accepted'])
        self.assertEqual(b.sampling_physics_steps,960)

    def test_verified_inside_fallback_does_not_swallow_external_cancellation(self):
        b,held,target,contents,state,records,modules=self.fixture()
        fallback={'state':b.og.sim.dump_state(), 'particles':[], 'candidate':{'candidate':0}}
        b.deadline.check=lambda **kwargs:(_ for _ in ()).throw(SkillError('episode_cancelled','cancelled'))
        with patch.dict('sys.modules',modules),self.assertRaises(SkillError) as caught:
            sample_assembly_inside(b,held,target,contents,open_fallback=fallback)
        self.assertEqual(caught.exception.code,'episode_cancelled')
        self.assertFalse(any(r.get('status')=='inside_candidate_accepted' for r in records))

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

    def test_nested_particle_ownership_does_not_require_outer_volume(self):
        b,held,target,contents,state,records,modules=self.fixture(collide=False,oversize=True)
        b.env=SimpleNamespace(scene=object())
        b._placement_particle_payload=(held,[object()])
        def tall_contents(*args):
            # A carrier's center can satisfy native Inside while its retained
            # contents extend past the outer fill annotation (R670 pot/sink).
            # Geometry remains a proposal input; ownership decides retention.
            return [(held.position-b.torch.tensor([.6,.1,.1]),
                     held.position+b.torch.tensor([.6,.1,.1]))]
        with patch.dict('sys.modules',modules), \
             patch('manipulation_agent.executors.particle_payload.particle_bounds', side_effect=tall_contents), \
             patch('manipulation_agent.executors.particle_payload.verify_particles') as check:
            self.assertTrue(sample_assembly_inside(b,held,target,contents,max_candidates=1))
        check.assert_called_once()
        self.assertEqual(records[-1]['domain_attempts'],{'full_volume':1})
        self.assertTrue(held.states[modules['omnigibson.object_states'].Inside].get_value(target))

    def test_spilled_particle_payload_is_still_rejected(self):
        b,held,target,contents,state,records,modules=self.fixture(collide=False)
        b.env=SimpleNamespace(scene=object())
        b._placement_particle_payload=(held,[object()])
        with patch.dict('sys.modules',modules), \
             patch('manipulation_agent.executors.particle_payload.particle_bounds',return_value=[]), \
             patch('manipulation_agent.executors.particle_payload.verify_particles',
                   side_effect=SkillError('postcondition_error','foreign support')) as check:
            self.assertFalse(sample_assembly_inside(b,held,target,contents,max_candidates=1))
        self.assertEqual(float(held.position[0]),5.)
        self.assertEqual(records[-1]['rejections']['particle_payload_postcondition'],1)
        check.assert_called_once()

    def test_particle_identity_change_aborts_search_instead_of_trying_another_pose(self):
        b,held,target,contents,state,records,modules=self.fixture(collide=False)
        b.env=SimpleNamespace(scene=object());b._placement_particle_payload=(held,[object()])
        with patch.dict('sys.modules',modules), \
             patch('manipulation_agent.executors.particle_payload.particle_bounds',return_value=[]), \
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

    def test_open_only_fallback_restores_its_particle_frames_with_the_pose(self):
        b,held,target,contents,state,records,modules=self.fixture(collide=False)
        snapshots=[];restored=[]
        def snapshot(sim):
            saved={'position':held.position.clone()}
            snapshots.append(saved)
            return saved
        with patch.dict('sys.modules',modules), \
             patch('manipulation_agent.executors.particle_payload.snapshot_particle_state',side_effect=snapshot), \
             patch('manipulation_agent.executors.particle_payload.particle_state_unchanged',return_value=True), \
             patch('manipulation_agent.executors.particle_payload.restore_particle_state',side_effect=restored.append), \
             patch('manipulation_agent.executors.closure_clearance.check_closure_clearance',return_value={'clear':False}):
            self.assertTrue(sample_assembly_inside(b,held,target,contents,max_candidates=2))
        self.assertEqual(len(snapshots),2)
        self.assertIs(restored[-1],snapshots[1])
        self.assertTrue(b.torch.equal(held.position,snapshots[1]['position']))
        self.assertEqual(records[-2]['closure_clearance'],'open_only_fallback')

    def test_open_only_fallback_never_restores_changed_particle_identities(self):
        b,held,target,contents,state,records,modules=self.fixture(collide=False)
        with patch.dict('sys.modules',modules), \
             patch('manipulation_agent.executors.particle_payload.particle_state_unchanged',return_value=False), \
             patch('manipulation_agent.executors.closure_clearance.check_closure_clearance',return_value={'clear':False}):
            with self.assertRaises(SkillError) as caught:
                sample_assembly_inside(b,held,target,contents,max_candidates=1)
        self.assertEqual(caught.exception.code,'target_changed')
        self.assertEqual(state['loads'],0)
        self.assertTrue(records[-1]['identity_changed'])


if __name__=='__main__':unittest.main()
