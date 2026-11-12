"""Collider enclosures must remain valid under the candidate root transform."""
import itertools
import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import patch
import numpy as np
from manipulation_agent.executors.placement_overlap import oriented_piece_box, assembly_overlap_ranker


class ColliderPieceBoundsTests(unittest.TestCase):
    def test_surface_ordering_ignores_receiver_but_keeps_contents_and_neighbors(self):
        import torch
        mesh = SimpleNamespace(points=torch.tensor(list(itertools.product((-.1,.1), repeat=3))),
                               scaled_transform=torch.eye(4))
        def body(path, meshes=None):
            return SimpleNamespace(links={'base': SimpleNamespace(prim_path=path, is_meta_link=False,
                collision_meshes=meshes or {})})
        held = body('/chip', {'mesh': mesh})
        held.get_position_orientation = lambda: (torch.zeros(3), torch.tensor([0.,0.,0.,1.]))
        receiver, existing, neighbor = [body(path) for path in ('/tray', '/existing', '/counter')]
        backend = SimpleNamespace(robot=body('/robot'), torch=torch,
            env=SimpleNamespace(scene=SimpleNamespace(objects=[held, receiver, existing, neighbor])),
            deadline=SimpleNamespace(check=lambda **kwargs: None))
        hits = []
        def overlap(half, center, orientation, callback, any_hit):
            for path in hits:
                if not callback(SimpleNamespace(rigid_body=path)):
                    break
        lazy = ModuleType('omnigibson.lazy')
        lazy.omni = SimpleNamespace(physx=SimpleNamespace(
            get_physx_scene_query_interface=lambda: SimpleNamespace(overlap_box=overlap)))
        utils = ModuleType('omnigibson.utils')
        utils.transform_utils = SimpleNamespace(quat2mat=lambda q: torch.eye(3),
            mat2quat=lambda m: torch.tensor([0.,0.,0.,1.]))
        modules = {'omnigibson': ModuleType('omnigibson'), 'omnigibson.lazy': lazy,
                   'omnigibson.utils': utils}
        with patch.dict(sys.modules, modules):
            for paths, expected in ((['/tray'], 0), (['/tray', '/existing'], 1),
                                    (['/tray', '/counter'], 1)):
                with self.subTest(paths=paths):
                    hits[:] = paths
                    ordinary = assembly_overlap_ranker(backend, held, [held])
                    supported = assembly_overlap_ranker(backend, held, [held], support_objects=(receiver,))
                    pose = held.get_position_orientation()
                    self.assertEqual(ordinary(*pose), 1)
                    self.assertEqual(supported(*pose), expected)
            # The table under a thin plate is declared by path and no longer ranks as a neighbor.
            hits[:] = ['/tray', '/counter']
            thin = assembly_overlap_ranker(backend, held, [held], support_objects=(receiver,),
                                           support_paths=('/counter',))
            self.assertEqual(thin(*held.get_position_orientation()), 0)
            hits[:] = ['/tray', '/counter', '/existing']
            thin = assembly_overlap_ranker(backend, held, [held], support_objects=(receiver,),
                                           support_paths=('/counter',))
            self.assertEqual(thin(*held.get_position_orientation()), 1)

    def test_rotated_scaled_piece_and_new_root_pose_enclose_every_vertex(self):
        vertices=np.array(list(itertools.product((-.3,.3),(-.04,.04),(-.01,.01))))
        angle=.63;c,s=np.cos(angle),np.sin(angle)
        source_rotation=np.array([[c,-s,0],[s,c,0],[0,0,1]])
        points=vertices@source_rotation.T+[.12,-.08,.23]
        center,half,axes=oriented_piece_box(points)
        root_rotation=np.array([[0,0,1],[1,0,0],[0,1,0]])
        root_position=np.array([4.,-2.,.8])
        world_points=points@root_rotation.T+root_position
        world_center=root_position+root_rotation@center
        world_axes=root_rotation@axes
        self.assertTrue(np.all(np.abs((world_points-world_center)@world_axes)<=half+1e-12))
        self.assertAlmostEqual(np.linalg.det(world_axes),1.)
        self.assertLess(np.prod(2*half),np.prod(np.ptp(points,axis=0)))

    def test_planar_piece_is_finite_and_not_a_rejection_certificate(self):
        points=np.array([[0.,0.,0.],[1.,0.,0.],[1.,1.,0.],[0.,1.,0.]])
        center,half,axes=oriented_piece_box(points)
        self.assertTrue(np.all(np.isfinite(half)))
        self.assertTrue(np.all(half>0))
        self.assertTrue(np.all(np.abs((points-center)@axes)<=half+1e-12))
