"""Collider enclosures must remain valid under the candidate root transform."""
import itertools
import unittest
import numpy as np
from manipulation_agent.executors.placement_overlap import oriented_piece_box


class ColliderPieceBoundsTests(unittest.TestCase):
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
