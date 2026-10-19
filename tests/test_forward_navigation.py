"""Forward movement and heading search; these are CPU controller tests."""
import importlib.util
import math
import unittest

from manipulation_agent.executors.forward_navigation import plan_forward_route, angle
from manipulation_agent.executors.gt_navigation import GridMap, NavigationPlan, GreedyGridFollower, NavigationError


def plan(points, final_yaw=0.):
    return NavigationPlan(tuple(points), points[-1], points[-1], final_yaw,
                          sum(math.dist(a,b) for a,b in zip(points,points[1:])),
                          0., 1, 1, 0., 0, travel_yaw=0.)


class PointCollision:
    def __init__(self):
        self.grid = GridMap(61,61,.1,(-3.,-3.),bytes([1])*61**2)

    def motion_free(self, a, b):
        return self.grid.segment_free(a[:2], b[:2])


class ForwardNavigationTests(unittest.TestCase):
    def follow(self, route, start, collision, noisy=False):
        actual = start
        moves = []
        for phase in route.phases:
            follower = GreedyGridFollower(collision.grid,phase,1/30,forward_only=True,
                                          motion_check=collision.motion_free)
            for _ in range(2000):
                command = follower.next_pose(actual)
                if command is None:
                    break
                dx,dy = command[0]-actual[0],command[1]-actual[1]
                distance = math.hypot(dx,dy)
                self.assertLessEqual(distance,.5/30+1e-9)
                self.assertLessEqual(abs(angle(command[2]-actual[2])),math.pi/90+1e-9)
                if distance>.002:
                    self.assertGreater(dx*math.cos(actual[2])+dy*math.sin(actual[2]),0.)
                    self.assertLess(abs(angle(math.atan2(dy,dx)-actual[2])),.16)
                moves.append((actual,command))
                actual = ((command[0]+6.103515625e-5,command[1],command[2]-.000105527829)
                          if noisy else command)
            else:
                self.fail('Forward phase did not converge')
        self.assertLess(math.dist(actual[:2],route.phases[-1].goal),.002)
        self.assertLess(abs(angle(actual[2]-route.phases[-1].final_yaw)),.001)
        return moves

    def test_side_and_rear_goals_turn_before_translation(self):
        collision=PointCollision()
        for goal in [(0.,1.),(-1.,0.),(0.,.01)]:
            with self.subTest(goal=goal):
                route=plan_forward_route(plan([(0.,0.),goal]),(0.,0.,0.),collision)
                moves=self.follow(route,(0.,0.,0.),collision)
                self.assertEqual(moves[0][0][:2],moves[0][1][:2])
                self.assertGreater(abs(moves[0][1][2]),0.)

    def test_l_shaped_path_turns_at_corner_and_restores_final_view(self):
        collision=PointCollision()
        route=plan_forward_route(plan([(0.,0.),(1.,0.),(1.,1.)],-.6),(0.,0.,math.pi),collision)
        self.assertEqual(route.method,'certified_polyline')
        self.follow(route,(0.,0.,math.pi),collision,noisy=True)
        self.assertAlmostEqual(route.distance_m,2.)

    def test_narrow_stance_requires_forward_exit_before_turn(self):
        class Corridor(PointCollision):
            def motion_free(self,a,b):
                # A rectangular body can drive along this aisle but cannot
                # turn until it reaches the open area at x >= .5.
                if min(a[0],b[0])<.5-1e-9 and (abs(angle(a[2]))>.001 or abs(angle(b[2]))>.001):
                    return False
                return super().motion_free(a,b)
        collision=Corridor()
        route=plan_forward_route(plan([(0.,0.),(1.,1.)],math.pi/2),(0.,0.,0.),collision)
        self.assertEqual(route.method,'heading_lattice')
        self.assertGreater(route.expanded_states,0)
        self.follow(route,(0.,0.,0.),collision)

    def test_collision_at_final_orientation_is_not_ignored(self):
        class NoTurns(PointCollision):
            def motion_free(self,a,b):
                return abs(angle(a[2]))<.001 and abs(angle(b[2]))<.001 and super().motion_free(a,b)
        with self.assertRaises(NavigationError):
            plan_forward_route(plan([(0.,0.),(1.,0.)],math.pi/2),(0.,0.,0.),NoTurns())

    def test_narrow_start_and_goal_keep_continuous_non_lattice_headings(self):
        target=(1.4,1.)
        class NarrowEnds(PointCollision):
            def optimistic_grid(self):
                # The continuous final heading fits, but none of the eight
                # lattice headings fits the final aisle in this relaxation.
                free=bytearray(self.grid.free)
                for r in range(self.grid.height):
                    for c in range(self.grid.width):
                        if math.dist(self.grid.world((r,c)),target)<.3:
                            free[r*self.grid.width+c]=0
                return GridMap(self.grid.width,self.grid.height,self.grid.resolution,self.grid.origin,bytes(free))
            def motion_free(self,a,b):
                for p in (a,b):
                    if math.hypot(*p[:2])<.4-1e-9 and abs(angle(p[2]-.3))>.001:
                        return False
                    if math.dist(p[:2],target)<.25-1e-9 and abs(angle(p[2]-1.2))>.001:
                        return False
                return super().motion_free(a,b)
        collision=NarrowEnds()
        route=plan_forward_route(plan([(0.,0.),target],1.2),(0.,0.,.3),collision,max_expansions=500)
        self.assertEqual(route.method,'heading_lattice')
        self.follow(route,(0.,0.,.3),collision)

    def test_cancellation_and_heading_expansion_limit(self):
        def cancelled():raise TimeoutError('cancelled')
        with self.assertRaisesRegex(TimeoutError,'cancelled'):
            plan_forward_route(plan([(0.,0.),(1.,0.)]),(0.,0.,0.),PointCollision(),check_cancelled=cancelled)
        class NoTurns(PointCollision):
            def motion_free(self,a,b):
                return abs(angle(a[2]))<.001 and abs(angle(b[2]))<.001 and super().motion_free(a,b)
        with self.assertRaisesRegex(NavigationError,'budget'):
            plan_forward_route(plan([(0.,0.),(0.,1.)]),(0.,0.,0.),NoTurns(),max_expansions=1)

    def test_runtime_collision_change_stops_before_command(self):
        collision=PointCollision()
        follower=GreedyGridFollower(collision.grid,plan([(0.,0.),(1.,0.)]),1/30,
                                    forward_only=True,motion_check=lambda a,b:False)
        with self.assertRaisesRegex(NavigationError,'swept collision'):
            follower.next_pose((0.,0.,0.))

    def test_short_translation_cannot_skip_initial_heading(self):
        collision=PointCollision()
        p=plan([(0.,0.),(0.,.01)],math.pi/2)
        from dataclasses import replace
        p=replace(p,travel_yaw=math.pi/2)
        follower=GreedyGridFollower(collision.grid,p,1/30,forward_only=True)
        command=follower.next_pose((0.,0.,0.))
        self.assertEqual(command[:2],(0.,0.))
        self.assertGreater(command[2],0.)


@unittest.skipUnless(importlib.util.find_spec('cv2'),'Requires OpenCV; no simulator is started')
class HeadingCollisionTests(unittest.TestCase):
    def checker(self,raw,footprint,reference_yaw=0.):
        import numpy as np
        from manipulation_agent.executors.heading_collision import HeadingCollisionChecker
        grid=GridMap(raw.shape[1],raw.shape[0],.05,(-1.,-1.),np.ones_like(raw).tobytes())
        return HeadingCollisionChecker(grid,[(raw,footprint)],reference_yaw,np.ones_like(raw))

    def test_turn_checks_intermediate_body_sweep(self):
        import numpy as np
        raw=np.ones((41,41),dtype='uint8');raw[25,25]=0
        footprint=[[-.4,-.04],[.4,-.04],[.4,.04],[-.4,.04]]
        checker=self.checker(raw,footprint)
        self.assertTrue(checker.motion_free((0.,0.,0.),(0.,0.,0.)))
        self.assertTrue(checker.motion_free((0.,0.,math.pi/2),(0.,0.,math.pi/2)))
        self.assertFalse(checker.motion_free((0.,0.,0.),(0.,0.,math.pi/2)))

    def test_translation_uses_current_heading_footprint(self):
        import numpy as np
        raw=np.ones((41,41),dtype='uint8');raw[24,:]=0
        footprint=[[-.35,-.04],[.35,-.04],[.35,.04],[-.35,.04]]
        checker=self.checker(raw,footprint)
        self.assertTrue(checker.motion_free((-.3,0.,0.),(.3,0.,0.)))
        self.assertFalse(checker.motion_free((-.3,0.,math.pi/2),(.3,0.,math.pi/2)))

    def test_world_footprint_is_rotated_relative_to_recorded_heading(self):
        import numpy as np
        raw=np.ones((41,41),dtype='uint8');raw[24,:]=0
        footprint=[[-.04,-.35],[.04,-.35],[.04,.35],[-.04,.35]]
        checker=self.checker(raw,footprint,math.pi/2)
        self.assertTrue(checker.motion_free((-.3,0.,0.),(.3,0.,0.)))
        self.assertFalse(checker.motion_free((-.3,0.,math.pi/2),(.3,0.,math.pi/2)))


if __name__=='__main__':unittest.main()
