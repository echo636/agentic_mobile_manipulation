import math
import unittest
from manipulation_agent.executors.holonomic_navigation_frames import (candidate_world_pose,world_pose_to_base_joints,
    base_joints_to_world_pose,intrinsic_matrix,intrinsic_angles)


class FrameTests(unittest.TestCase):
    def vector_close(self,a,b):
        for x,y in zip(a,b):self.assertAlmostEqual(x,y,places=7)
    def pose_close(self,a,b):
        self.vector_close(a[0],b[0])
        for ar,br in zip(a[1],b[1]):self.vector_close(ar,br)

    def test_identity_anchor_preserves_native_planar_joint_values(self):
        identity=intrinsic_matrix([0,0,0])
        desired=candidate_world_pose([1.2,-2.4,.7],[0,0,.12],identity)
        joints=world_pose_to_base_joints(*desired,[0,0,0],identity)
        self.vector_close(joints,[1.2,-2.4,.12,0,0,.7])

    def test_translated_rotated_anchor_uses_local_not_world_values(self):
        anchor=([10,20,.4],intrinsic_matrix([0,0,math.pi/2]))
        desired=([10,21,.6],intrinsic_matrix([0,0,math.pi/2]))
        joints=world_pose_to_base_joints(*desired,*anchor)
        self.vector_close(joints,[1,0,.2,0,0,0])
        self.pose_close(base_joints_to_world_pose(joints,*anchor),desired)
        wrong=base_joints_to_world_pose([10,21,.2,0,0,math.pi/2],*anchor)
        self.assertGreater(sum((a-b)**2 for a,b in zip(wrong[0],desired[0])),100)

    def test_endpoint_preserves_world_height_roll_pitch_not_anchor_values(self):
        root=([3,-4,.5],intrinsic_matrix([.1,-.2,.8]))
        old_local=[.4,.3,.2,-.3,.15,-.5]
        world=base_joints_to_world_pose(old_local,*root)
        desired=candidate_world_pose([1,2,1.3],*world)
        self.assertAlmostEqual(desired[0][2],world[0][2])
        self.assertNotAlmostEqual(desired[0][2],old_local[2])
        self.vector_close(intrinsic_angles(desired[1])[:2],intrinsic_angles(world[1])[:2])
        corrected=world_pose_to_base_joints(*desired,*root)
        self.pose_close(base_joints_to_world_pose(corrected,*root),desired)

    def test_recorded_300m_anchor_does_not_shift_candidate_or_sink_endpoint(self):
        identity=intrinsic_matrix([0,0,0]);anchor=([300,300,300],identity)
        world_position=[2.724416971,9.210571289,.005279541]
        desired=candidate_world_pose([-1.436877728,8.386918068,.555920124],world_position,identity)
        joints=world_pose_to_base_joints(*desired,*anchor)
        self.vector_close(joints[:3],[-301.436877728,-291.613081932,-299.994720459])
        self.pose_close(base_joints_to_world_pose(joints,*anchor),desired)
        self.assertGreater(desired[0][2],0)
        self.assertLess(joints[2],-299)

    def test_intrinsic_singular_branches_reconstruct_same_rotation(self):
        for pitch in [math.pi/2,-math.pi/2]:
            matrix=intrinsic_matrix([.3,pitch,-.8])
            recovered=intrinsic_matrix(intrinsic_angles(matrix))
            for actual,expected in zip(recovered,matrix):self.vector_close(actual,expected)

    def test_candidate_order_and_input_poses_are_unchanged(self):
        candidates=[[2,3,.1],[-4,5,-2.7],[0,1,3.1]]
        before=[row[:] for row in candidates]
        anchor=([-2,1,.3],intrinsic_matrix([0,0,-.9]))
        desired=[candidate_world_pose(p,[0,0,.6],intrinsic_matrix([0,0,0])) for p in candidates]
        recovered=[base_joints_to_world_pose(world_pose_to_base_joints(*p,*anchor),*anchor) for p in desired]
        for actual,expected in zip(recovered,desired):self.pose_close(actual,expected)
        self.assertEqual(candidates,before)


if __name__=='__main__':unittest.main()
