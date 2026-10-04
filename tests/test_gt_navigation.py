import math
import unittest

from manipulation_agent.executors.gt_navigation import (
    GridMap, GreedyGridFollower, NavigationError, NavigationPlan, candidate_score, plan_navigation,
)


def grid(width=81, height=81, resolution=.05, blocked=()):
    cells=bytearray([1])*(width*height)
    for row,col in blocked:cells[row*width+col]=0
    return GridMap(width,height,resolution,(0.,0.),bytes(cells))


class GTNavigationTests(unittest.TestCase):
    def test_archived_mousetrap_pose_does_not_enter_neighbor_obstacle(self):
        # Actual failure: [-.75, 2.25] acquired sub-micrometre settling error.
        g=GridMap(3,3,.05,(-.8,2.2),bytes([0,0,0,0,1,0,0,0,0]))
        pose=(-.750000536441803,2.249999523162842)
        self.assertEqual(g.cell(pose),(1,1))
        self.assertTrue(g.navigable(g.cell(pose)))
        self.assertLess(math.dist(pose,g.world((1,1))),1e-6)

    def test_grid_round_trip_is_stable_under_small_settling_errors(self):
        g=grid()
        for row,col in ((1,1),(7,17),(67,72)):
            x,y=g.world((row,col))
            for dx,dy in ((-1e-5,1e-5),(1e-5,-1e-5),(0.,0.)):
                self.assertEqual(g.cell((x+dx,y+dy)),(row,col))

    def test_diagonal_motion_cannot_cut_obstacle_corner(self):
        g=grid(2,2,1.,blocked=((0,1),(1,0)))
        self.assertFalse(g.segment_free((0.,0.),(1.,1.)))
        self.assertEqual(list(g.neighbors((0,0))),[])

    def test_snap_is_bounded_and_does_not_invent_free_space(self):
        g=GridMap(3,3,1.,(0.,0.),bytes([1,0,0,0,0,0,0,0,0]))
        self.assertIsNone(g.snap((1.,1.),.75))
        self.assertEqual(g.snap((.1,.1),.2),(0,0))

    def test_projection_tries_visible_cell_beyond_rejected_nearest_cell(self):
        g=grid(41,41,.1)
        # None of the discrete radial samples rounds to this narrow visible
        # opening, but it is well within the allowed projection distance.
        allowed=g.world((22,22))
        plan=plan_navigation(g,(.5,.5),(2.,2.),candidate_filter=lambda xy:xy==allowed)
        self.assertEqual(plan.goal,allowed)
        self.assertTrue(all(g.segment_free(a,b) for a,b in zip(plan.points,plan.points[1:])))

    def test_snap_rejects_disconnected_nearest_without_crossing_wall(self):
        g=grid(61,41,.1,blocked=[(r,15) for r in range(41)])
        # Only two visible cells: nearest radial projections lie across the
        # wall, while a bounded alternative is on the start's side.
        reachable=g.world((20,14));isolated=g.world((20,16))
        plan=plan_navigation(g,(.5,2.),(2.8,2.),candidate_filter=lambda xy:xy in (reachable,isolated))
        self.assertEqual(plan.goal,reachable)
        self.assertTrue(all(x<1.5 for x,y in plan.points))

    def test_really_occupied_start_is_rejected_without_teleport(self):
        g=grid(blocked=((10,10),))
        with self.assertRaisesRegex(NavigationError,'Start'):
            plan_navigation(g,(.5,.5),(2.,2.))

    def test_disconnected_goal_is_not_reached_through_a_wall(self):
        g=grid(161,81,blocked=[(r,60) for r in range(81)])
        with self.assertRaisesRegex(NavigationError,'reachable'):
            plan_navigation(g,(1.,2.),(6.,2.))

    def test_route_uses_doorway_and_every_segment_is_traversable(self):
        g=grid(101,101,blocked=[(r,40) for r in range(101) if not 70<=r<=80])
        plan=plan_navigation(g,(.5,.5),(4.,.5))
        self.assertTrue(any(y>3.4 for x,y in plan.points))
        self.assertGreater(plan.geodesic_m,math.dist(plan.points[0],plan.goal))
        self.assertTrue(all(g.segment_free(a,b) for a,b in zip(plan.points,plan.points[1:])))

    def test_ranking_penalizes_no_progress_like_reference_policy(self):
        self.assertGreater(candidate_score(.7,0.,.1),candidate_score(.7,0.,1.))
        self.assertLess(candidate_score(.7,.05,1.),candidate_score(1.2,0.,1.))

    def test_search_and_projection_budgets_are_enforced(self):
        g=grid()
        with self.assertRaisesRegex(NavigationError,'expansion budget'):
            plan_navigation(g,(.1,.1),(3.,3.),max_expansions=2)
        with self.assertRaisesRegex(NavigationError,'projection'):
            plan_navigation(g,(.1,.1),(100.,100.))

    def test_feedback_follower_reaches_goal_with_bounded_motion(self):
        g=grid();plan=plan_navigation(g,(.5,.5),(3.,2.))
        follower=GreedyGridFollower(g,plan,1/30);pose=(.5,.5,math.pi)
        for _ in range(2000):
            new=follower.next_pose(pose)
            if new is None:break
            self.assertLessEqual(math.dist(new[:2],pose[:2]),.5/30+1e-9)
            self.assertLessEqual(abs(follower.angle(new[2]-pose[2])),math.pi/90+1e-9)
            self.assertTrue(g.segment_free(pose[:2],new[:2]));pose=new
        else:self.fail('Follower did not terminate')
        self.assertLessEqual(math.dist(pose[:2],plan.goal),.002)
        self.assertLessEqual(abs(follower.angle(pose[2]-plan.final_yaw)),1e-4)

    def test_stalled_robot_does_not_report_success(self):
        g=grid();plan=plan_navigation(g,(.5,.5),(3.,2.));follower=GreedyGridFollower(g,plan,1/30,stall_steps=3)
        with self.assertRaisesRegex(NavigationError,'no progress'):
            for _ in range(5):follower.next_pose((.5,.5,0.))

    def test_recorded_060_pose_residual_does_not_block_forward_motion(self):
        # Archived task060 median feedback residuals. The old 1e-4 rad gate
        # keeps turning indefinitely under this measured 1.055e-4 rad error.
        g=grid();plan=plan_navigation(g,(.5,.5),(2.,.5))
        follower=GreedyGridFollower(g,plan,1/30);pose=(.5,.5,0.)
        for step in range(200):
            command=follower.next_pose(pose)
            if command is None:break
            self.assertTrue(g.segment_free(pose[:2],command[:2]))
            pose=(command[0]+6.103515625e-5,command[1],command[2]-0.00010552782906014802)
        else:self.fail('Measured settling residual trapped the follower in microturns')
        self.assertLess(step,100)
        self.assertLessEqual(math.dist(pose[:2],plan.goal),.002)
        self.assertLessEqual(abs(follower.angle(pose[2]-plan.final_yaw)),1e-3)

    def test_tiny_drift_and_yaw_jitter_are_not_translation_progress(self):
        g=grid();plan=plan_navigation(g,(.5,.5),(2.,.5))
        follower=GreedyGridFollower(g,plan,1/30)
        # Translation commands are not executed. Even drift *toward* the goal
        # must not reset the stall counter every frame, as the old code did.
        with self.assertRaisesRegex(NavigationError,'no progress'):
            for step in range(25):
                follower.next_pose((.5+step*6.103515625e-5,.5,(-1)**step*0.00010552782906014802))

    def test_yaw_jitter_does_not_mask_a_stalled_large_turn(self):
        g=grid();plan=plan_navigation(g,(.5,.5),(2.,.5))
        follower=GreedyGridFollower(g,plan,1/30)
        with self.assertRaisesRegex(NavigationError,'no progress'):
            for step in range(25):
                follower.next_pose((.5,.5,math.pi/2+(-1)**step*0.00010552782906014802))

    def test_turn_and_follow_around_obstacle_under_recorded_noise(self):
        g=grid(31,31,.1,blocked=((10,10),))
        points=((.5,.5),(1.5,.5),(1.5,1.5))
        plan=NavigationPlan(points,points[-1],(1.5,2.2),math.pi/2,2.,0.,1,1,0.,0)
        self.assertFalse(g.segment_free(points[0],points[-1]))
        follower=GreedyGridFollower(g,plan,1/30);pose=(*points[0],math.pi)
        for step in range(400):
            command=follower.next_pose(pose)
            if command is None:break
            self.assertLessEqual(math.dist(pose[:2],command[:2]),.5/30+1e-9)
            self.assertLessEqual(abs(follower.angle(command[2]-pose[2])),math.pi/90+1e-9)
            self.assertTrue(g.segment_free(pose[:2],command[:2]))
            pose=(command[0]+6.103515625e-5,command[1],command[2]-0.00010552782906014802)
        else:self.fail('Noisy corner traversal failed to finish')
        self.assertLessEqual(math.dist(pose[:2],plan.goal),.002)

    def test_pose_divergence_and_invalid_state_stop_follower(self):
        g=grid();plan=plan_navigation(g,(.5,.5),(3.,2.));follower=GreedyGridFollower(g,plan,1/30)
        follower.next_pose((.5,.5,0.))
        with self.assertRaisesRegex(NavigationError,'diverged'):follower.next_pose((2.,2.,0.))
        with self.assertRaises(NavigationError):follower.next_pose((float('nan'),0.,0.))


if __name__=='__main__':unittest.main()
