"""Forward movement and heading search; these are CPU controller tests."""
import importlib.util
import math
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from manipulation_agent.executors.forward_navigation import (
    plan_forward_route, angle, local_exit_certificate, checked_connection,
    simplify_forward_poses, motion_seconds,
    select_forward_route,
    route_from_poses,
)
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
    def test_connection_rejects_nearly_tangent_shortcut_before_feedback_drift(self):
        import numpy as np
        from manipulation_agent.executors.heading_collision import HeadingCollisionChecker
        support = np.ones((5, 5), dtype='uint8')
        support[1, 1] = 0
        grid = GridMap(5, 5, 1., (0., 0.), support.tobytes())
        collision = HeadingCollisionChecker(grid, [], 0., support)
        # Pass one micrometre below the blocked cell's bottom-right corner.
        start = (.75, -.250001, math.pi/4)
        end = (2.25, 1.249999, math.pi/4)
        self.assertTrue(collision.motion_free(start, end))
        drifted = (start[0], start[1]+.000002, start[2])
        self.assertFalse(collision.motion_free(drifted, end))
        self.assertIsNone(checked_connection(start, end[:2], end[2], collision))
        clear_start = (start[0], start[1]-.01, start[2])
        clear_end = (end[0], end[1]-.01, end[2])
        self.assertIsNotNone(checked_connection(clear_start, clear_end[:2], clear_end[2], collision))
        # Reserving space during planning must not weaken live collision checks.
        self.assertFalse(collision.motion_free(drifted, end))

    def test_connection_preserves_clear_long_turn_around_blocked_short_arc(self):
        class ShelfBesidePayload(PointCollision):
            block_other_side = False
            def motion_free(self, a, b):
                for i in range(101):
                    yaw = angle(a[2]+angle(b[2]-a[2])*i/100)
                    if .3 < yaw < 1.2:
                        return False
                    if self.block_other_side and -1.2 < yaw < -.3:
                        return False
                return super().motion_free(a, b)

        collision = ShelfBesidePayload()
        start = (0., 0., 0.)
        target = (0., 0., math.pi/2)
        self.assertFalse(collision.motion_free(start, target))
        poses = checked_connection(start, target[:2], target[2], collision)
        self.assertIsNotNone(poses)
        route = route_from_poses(plan([(0., 0.)], target[2]), poses, collision)
        executed = [start]+[(*p.goal, p.final_yaw) for p in route.phases]
        self.assertAlmostEqual(sum(abs(angle(b[2]-a[2])) for a,b in zip(executed, executed[1:])), 3*math.pi/2)
        self.assertTrue(all(collision.motion_free(a,b) for a,b in zip(executed, executed[1:])))
        collision.block_other_side = True
        self.assertIsNone(checked_connection(start, target[:2], target[2], collision))

    def test_explicit_complete_scene_budget_allows_work_but_still_expires(self):
        clock=[100.]
        original=plan_forward_route
        def search(*args,**kwargs):
            result=original(*args,**kwargs)
            clock[0]+=25.
            kwargs['check_cancelled']()
            return result
        with patch('manipulation_agent.executors.gt_navigation.time.monotonic',side_effect=lambda:clock[0]), \
                patch('manipulation_agent.executors.forward_navigation.plan_forward_route',side_effect=search):
            _,route,details=select_forward_route([plan([(0.,0.),(1.,0.)])],
                (0.,0.,0.),PointCollision(),planning_seconds=60.)
            self.assertAlmostEqual(route.distance_m,1.)
            self.assertEqual(details['total_seconds'],60.)
            with self.assertRaises(NavigationError) as caught:
                select_forward_route([plan([(0.,0.),(1.,0.)])],
                    (0.,0.,0.),PointCollision(),planning_seconds=20.)
            self.assertEqual(caught.exception.code,'navigation_search_exhausted')

    def test_valid_complete_route_can_use_more_than_two_seconds(self):
        clock = [100.]
        original = plan_forward_route

        def slower_search(*args, **kwargs):
            result = original(*args, **kwargs)
            clock[0] += 4.6
            kwargs['check_cancelled']()
            return result

        with patch('manipulation_agent.executors.gt_navigation.time.monotonic', side_effect=lambda: clock[0]), \
                patch('manipulation_agent.executors.forward_navigation.plan_forward_route', side_effect=slower_search):
            _, route, details = select_forward_route(
                [plan([(0., 0.), (1., 0.)])], (0., 0., 0.), PointCollision())
        self.assertAlmostEqual(route.distance_m, 1.)
        self.assertAlmostEqual(clock[0] - 100., 4.6)
        self.assertEqual(details['total_seconds'], 10.)
        self.assertEqual(details['search'], 'shared_multi_goal_continuous_pose')

    def test_slow_candidates_still_share_a_bounded_total_budget(self):
        from dataclasses import replace
        clock = [100.]

        def exhausted_search(*args, **kwargs):
            clock[0] += 10.1
            kwargs['check_cancelled']()
            self.fail('Exhausted candidate must not return an unchecked route')

        options = [replace(plan([(0., 0.), (1., 0.)]), final_yaw=i*.1) for i in range(6)]
        with patch('manipulation_agent.executors.gt_navigation.time.monotonic', side_effect=lambda: clock[0]), \
                patch('manipulation_agent.executors.forward_navigation.plan_forward_route', side_effect=exhausted_search) as search:
            with self.assertRaises(NavigationError) as caught:
                select_forward_route(options, (0., 0., 0.), PointCollision())
        self.assertEqual(caught.exception.code, 'navigation_search_exhausted')
        self.assertEqual(search.call_count, 1)
        self.assertEqual(len(search.call_args.kwargs['alternatives']), 5)
        self.assertEqual(len(caught.exception.attempts), 6)

    def test_search_from_narrow_goal_still_executes_only_forward_edges(self):
        target = (1.2,.8)
        class NarrowGoal(PointCollision):
            def motion_free(self,a,b):
                dx,dy = b[0]-a[0],b[1]-a[1]
                if math.hypot(dx,dy) > .002 and dx*math.cos(a[2])+dy*math.sin(a[2]) < 0.:
                    return False
                for i in range(101):
                    x,y = a[0]+dx*i/100,a[1]+dy*i/100
                    yaw = a[2]+angle(b[2]-a[2])*i/100
                    if math.dist((x,y),target) < .4 and abs(angle(yaw-.37)) > .001:
                        return False
                return super().motion_free(a,b)
        collision = NarrowGoal()
        start = (0.,0.,0.)
        route = plan_forward_route(plan([start[:2],target],.37),start,collision,max_expansions=1000)
        self.assertEqual(route.method,'continuous_pose_astar')
        self.follow(route,start,collision)
        # A legal but isolated first endpoint must not corrupt reconstruction
        # when the reverse search reaches a different root.
        isolated=(-1.5,-1.5)
        class TwoPockets(NarrowGoal):
            def motion_free(self,a,b):
                for i in range(101):
                    xy=(a[0]+(b[0]-a[0])*i/100,a[1]+(b[1]-a[1])*i/100)
                    if .05<math.dist(xy,isolated)<.3:return False
                return super().motion_free(a,b)
        collision=TwoPockets()
        route=plan_forward_route(plan([start[:2],isolated]),start,collision,
                                 alternatives=(plan([start[:2],target],.37),),max_expansions=1000)
        self.assertEqual(route.phases[-1].goal,target)
        self.follow(route,start,collision)

    def test_certified_route_survives_optional_shortcut_timeout(self):
        from manipulation_agent.executors.gt_navigation import SearchBudgetExhausted
        poses=[(0.,0.,0.),(1.,0.,0.),(1.,0.,math.pi/2),(1.,1.,math.pi/2)]
        collision=PointCollision()
        self.assertTrue(all(collision.motion_free(a,b) for a,b in zip(poses,poses[1:])))
        def expired():raise SearchBudgetExhausted()
        route=route_from_poses(plan([p[:2] for p in poses],math.pi/2),poses,collision,check_cancelled=expired)
        self.assertEqual(route.phases[-1].goal,poses[-1][:2])
        self.follow(route,poses[0],collision)
        def cancelled():raise RuntimeError('cancelled by caller')
        with self.assertRaisesRegex(RuntimeError,'cancelled by caller'):
            route_from_poses(plan([p[:2] for p in poses],math.pi/2),poses,collision,check_cancelled=cancelled)

    def test_bent_narrow_passage_keeps_continuous_headings_through_its_middle(self):
        class RotatedPassage(PointCollision):
            def __init__(self, yaw, barrier=False):
                super().__init__()
                self.yaw, self.barrier = yaw, barrier

            def guidance_grid(self):
                # A sampled-heading guide may omit this entire passage. It
                # must affect ordering only, never continuous reachability.
                g = self.grid
                return GridMap(g.width,g.height,g.resolution,g.origin,bytes(g.width*g.height))

            def motion_free(self, a, b):
                c, s = math.cos(self.yaw), math.sin(self.yaw)
                for i in range(101):
                    wx = a[0] + (b[0]-a[0])*i/100
                    wy = a[1] + (b[1]-a[1])*i/100
                    x, y = c*wx+s*wy, -s*wx+c*wy
                    yaw = angle(a[2]+angle(b[2]-a[2])*i/100-self.yaw)
                    if x < -.001 or (self.barrier and .5 < x < .55):
                        return False
                    if x < .85:
                        if not -.025 < y < .075 or not -.001 <= yaw <= .15:
                            return False
                        if x < .4 and abs(y) >= .025:
                            return False
                        if x >= .4 and y < .025 and yaw < .05:
                            return False
                return super().motion_free(a, b)

        for yaw in (.317, -.213, .583):
            with self.subTest(yaw=yaw):
                c, s = math.cos(yaw), math.sin(yaw)
                start = (0., 0., yaw)
                target = (c-.4*s, s+.4*c)
                collision = RotatedPassage(yaw)
                route = plan_forward_route(plan([start[:2], target], yaw+math.pi/2),
                                           start, collision, max_expansions=2000)
                self.follow(route, start, collision)
                self.assertTrue(any(.05 < angle(p.final_yaw-yaw) < .15 for p in route.phases))
                with self.assertRaises(NavigationError):
                    plan_forward_route(plan([start[:2], target], yaw+math.pi/2),
                                       start, RotatedPassage(yaw, True), max_expansions=500)

    def test_narrow_entry_needs_alignment_before_forward_motion_is_blocked(self):
        class EarlyAlignmentSlot(PointCollision):
            def __init__(self, barrier=False):
                self.grid = GridMap(61, 61, .05, (-1., -1.), bytes([1])*61**2)
                self.barrier = barrier

            def motion_free(self, a, b):
                for i in range(101):
                    x = a[0] + (b[0]-a[0])*i/100
                    y = a[1] + (b[1]-a[1])*i/100
                    yaw = angle(a[2] + angle(b[2]-a[2])*i/100)
                    if x < -.001 or (self.barrier and .5 < x < .55):
                        return False
                    if x < .85:
                        if not -.025 < y < .075 or not -.001 <= yaw <= .15:
                            return False
                        # A long front load can advance into the throat, but
                        # then lacks room to begin the small alignment turn.
                        if x > .25 and y < .025 and yaw > .01:
                            return False
                        if x >= .4 and y < .025:
                            return False
                return super().motion_free(a, b)

        start = (0., 0., 0.)
        target = (1., .4)
        collision = EarlyAlignmentSlot()
        self.assertTrue(collision.motion_free(start, (.2, 0., 0.)))
        route = plan_forward_route(plan([start[:2], target], math.pi/2),
                                   start, collision, max_expansions=500)
        self.follow(route, start, collision)
        with self.assertRaises(NavigationError):
            plan_forward_route(plan([start[:2], target], math.pi/2),
                               start, EarlyAlignmentSlot(True), max_expansions=500)

    def test_shallow_forward_exit_from_coarse_heading_dead_end(self):
        class BentSlot(PointCollision):
            def __init__(self,barrier=False):
                self.grid=GridMap(61,61,.05,(-1.,-1.),bytes([1])*61**2)
                self.barrier=barrier
            def motion_free(self,a,b):
                for i in range(101):
                    x=a[0]+(b[0]-a[0])*i/100
                    y=a[1]+(b[1]-a[1])*i/100
                    yaw=angle(a[2]+angle(b[2]-a[2])*i/100)
                    if x<-.001:return False
                    if self.barrier and .5<x<.55:return False
                    if x<.85:
                        if not -.025<y<.075 or not -.001<=yaw<=.15:return False
                        if x<.4 and abs(y)>=.025:return False
                        if x>=.4 and y<.025 and yaw<.05:return False
                return super().motion_free(a,b)
        start=(0.,0.,0.);target=(1.,.4)
        collision=BentSlot()
        route=plan_forward_route(plan([start[:2],target],math.pi/2),start,collision,max_expansions=500)
        self.assertEqual(route.method,'continuous_pose_astar')
        self.assertTrue(any(.01<abs(angle(p.final_yaw))<.15 for p in route.phases))
        self.follow(route,start,collision)
        # Legal endpoints do not allow a long connection through an obstacle.
        with self.assertRaises(NavigationError):
            plan_forward_route(plan([start[:2],target],math.pi/2),start,BentSlot(True),max_expansions=500)

    def test_outward_facing_pocket_also_requires_a_forward_entry(self):
        class Pocket(PointCollision):
            def motion_free(self,a,b):
                if max(a[0],b[0])>.01:return False
                if max(a[0],b[0])>-.15:
                    if abs(angle(b[2]-a[2]))>.05:return False
                    if min(abs(angle(a[2])),abs(angle(a[2]-math.pi)))>.05:return False
                return super().motion_free(a,b)
        collision=Pocket();pose=(0.,0.,math.pi)
        self.assertIsNotNone(local_exit_certificate(pose,collision))
        self.assertIsNone(local_exit_certificate(pose,collision,incoming=True))

    def test_complete_candidate_cost_avoids_unneeded_final_half_turn(self):
        from dataclasses import replace
        a=plan([(0.,0.),(1.,0.)],math.pi)
        b=replace(a,final_yaw=0.)
        selected,route,details=select_forward_route([a,b],(0.,0.,0.),PointCollision())
        self.assertEqual(selected.final_yaw,0.)
        self.assertAlmostEqual(route.turn_radians,0.)
        self.assertFalse(details['globally_optimal'])

    def test_local_exit_rejects_legal_but_forward_trapped_pose(self):
        class Pocket(PointCollision):
            def motion_free(self,a,b):
                if max(a[0],b[0])>.01:return False
                if max(a[0],b[0])>-.15 and max(abs(angle(a[2])),abs(angle(b[2])))>.05:return False
                return super().motion_free(a,b)
        collision=Pocket();pose=(0.,0.,0.)
        self.assertTrue(collision.motion_free(pose,pose))
        self.assertTrue(collision.motion_free(pose,(-.2,0.,0.)))
        self.assertIsNone(local_exit_certificate(pose,collision))
        certificate=local_exit_certificate((-.4,0.,0.),collision)
        self.assertIsNotNone(certificate)
        self.assertGreaterEqual(certificate['displacement_m'],.3-1e-9)

    def test_exit_keeps_narrow_forward_corridor_and_checks_full_motion(self):
        class Corridor(PointCollision):
            def motion_free(self,a,b):
                return abs(a[1])<.01 and abs(b[1])<.01 and abs(a[2])<.01 and abs(b[2])<.01 and super().motion_free(a,b)
        collision=Corridor();result=local_exit_certificate((0.,0.,0.),collision)
        self.assertIsNotNone(result)
        for a,b in zip(result['poses'],result['poses'][1:]):
            self.assertTrue(collision.motion_free(a,b))
            self.assertGreaterEqual((b[0]-a[0])*math.cos(a[2])+(b[1]-a[1])*math.sin(a[2]),-1e-9)

    def test_local_half_turn_exit_keeps_clear_direction_under_yaw_residual(self):
        # One side of a stance blocks the counterclockwise turn. Only the
        # backwards-facing forward departure is open. A pi-angle tie must
        # not discard the clear clockwise sweep after small pose feedback.
        for residual in (-1e-8, 0., 1e-6):
            yaw=-.9285902628196879+residual
            class Pocket(PointCollision):
                def motion_free(self,a,b):
                    if math.dist(a[:2],b[:2])>1e-10:
                        return abs(angle(a[2]-yaw-math.pi))<1e-6
                    delta=angle(b[2]-a[2])
                    return delta<=1e-9
            collision=Pocket();pose=(0.,0.,yaw)
            with self.subTest(residual=residual):
                result=local_exit_certificate(pose,collision)
                self.assertIsNotNone(result)
                self.assertGreaterEqual(result['displacement_m'],.3-1e-9)
                for a,b in zip(result['poses'],result['poses'][1:]):
                    self.assertTrue(collision.motion_free(a,b))
                    self.assertLessEqual(abs(angle(b[2]-a[2])),math.pi/2+1e-9)

    def test_numeric_cell_correction_does_not_demand_a_turn(self):
        class NoTurns(PointCollision):
            def motion_free(self,a,b):
                return abs(a[2])<.001 and abs(b[2])<.001 and super().motion_free(a,b)
        connection=checked_connection((.0000002,.0000004,0.),(0.,0.),0.,NoTurns())
        self.assertIsNotNone(connection)
        self.assertTrue(all(p[2]==0. for p in connection))

    def test_shortcuts_reduce_motion_time_and_preserve_final_pose(self):
        poses=[(0.,0.,0.),(1.,0.,0.),(1.,0.,math.pi/2),(1.,1.,math.pi/2),
               (1.,1.,math.pi),(0.,1.,math.pi),(0.,1.,math.pi/2)]
        result,count=simplify_forward_poses(poses,PointCollision())
        self.assertGreater(count,0)
        self.assertLess(motion_seconds(result),motion_seconds(poses))
        self.assertEqual(result[0],poses[0]);self.assertEqual(result[-1],poses[-1])

    def test_shortcuts_never_cross_an_obstacle(self):
        class Obstacle(PointCollision):
            def motion_free(self,a,b):
                for i in range(101):
                    x=a[0]+(b[0]-a[0])*i/100;y=a[1]+(b[1]-a[1])*i/100
                    if .35<x<.65 and -.2<y<.7:return False
                return super().motion_free(a,b)
        collision=Obstacle()
        poses=[(0.,0.,math.pi/2),(0.,1.,math.pi/2),(0.,1.,0.),(1.,1.,0.),
               (1.,1.,-math.pi/2),(1.,0.,-math.pi/2)]
        result,_=simplify_forward_poses(poses,collision)
        self.assertTrue(all(collision.motion_free(a,b) for a,b in zip(result,result[1:])))
        self.assertGreaterEqual(sum(math.dist(a[:2],b[:2]) for a,b in zip(result,result[1:])),1.4)

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

    def test_open_l_shaped_path_shortcuts_and_restores_final_view(self):
        collision=PointCollision()
        route=plan_forward_route(plan([(0.,0.),(1.,0.),(1.,1.)],-.6),(0.,0.,math.pi),collision)
        self.assertEqual(route.method,'certified_polyline')
        self.follow(route,(0.,0.,math.pi),collision,noisy=True)
        self.assertAlmostEqual(route.distance_m,math.sqrt(2))
        self.assertGreater(route.shortcuts,0)

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
        self.assertEqual(route.method,'continuous_pose_astar')
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
        self.assertEqual(route.method,'continuous_pose_astar')
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
    @unittest.skipUnless(importlib.util.find_spec('scipy'),'Requires optional roadmap dependency')
    def test_recorded_nearby_approach_avoids_forty_metre_house_detour(self):
        import hashlib
        import numpy as np
        from manipulation_agent.executors.heading_collision import HeadingCollisionChecker
        path=Path(__file__).parent/'fixtures/local_stances/garden_detour_r50.npz'
        meta=json.loads(path.with_suffix('.json').read_text())
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),meta['sha256'])
        with np.load(path,allow_pickle=False) as z:
            free=z['free'];grid=GridMap(free.shape[1],free.shape[0],float(z['resolution']),
                                      tuple(z['origin']),free.tobytes())
            footprints=json.loads(str(z['layer_footprints_json']))
            collision=HeadingCollisionChecker(grid,[(raw,row['footprint']) for raw,row in
                zip(z['raw_layers'],footprints)],float(z['initial_layer_yaw']),z['support'])
            actual=(*meta['start_xy'],float(z['initial_layer_yaw']))
        options=[NavigationPlan(**{**p,'points':tuple(map(tuple,p['points'])),
            'goal':tuple(p['goal']),'target':tuple(p['target'])}) for p in meta['endpoint_options']]
        chosen,route,_=select_forward_route(options,actual,collision,planning_seconds=30.)
        self.assertLess(route.distance_m,5.)
        self.assertIn((chosen.goal,chosen.final_yaw),[(p.goal,p.final_yaw) for p in options])
        poses=[actual]+[(*phase.goal,phase.final_yaw) for phase in route.phases]
        self.assertTrue(all(collision.motion_free(a,b) for a,b in zip(poses,poses[1:])))
        for a,b in zip(poses,poses[1:]):
            if math.dist(a[:2],b[:2])>.002:
                self.assertLess(abs(angle(math.atan2(b[1]-a[1],b[0]-a[0])-a[2])),1e-6)

    @unittest.skipUnless(importlib.util.find_spec('scipy'),'Requires optional roadmap dependency')
    def test_loaded_fridge_departure_reaches_original_visible_stance(self):
        import numpy as np
        from manipulation_agent.executors.heading_collision import HeadingCollisionChecker
        z=np.load(Path(__file__).parent/'fixtures/local_stances/kitchen_loaded_departure_r43.npz')
        free=z['free'];grid=GridMap(free.shape[1],free.shape[0],float(z['resolution']),
                                   tuple(z['origin']),free.tobytes())
        footprints=json.loads(str(z['layer_footprints_json']))
        collision=HeadingCollisionChecker(grid,[(raw,row['footprint']) for raw,row in
            zip(z['raw_layers'],footprints)],float(z['initial_layer_yaw']),z['support'])
        options=[NavigationPlan(**{**p,'points':tuple(map(tuple,p['points'])),
            'goal':tuple(p['goal']),'target':tuple(p['target'])}) for p in json.loads(str(z['options_json']))]
        actual=tuple(z['start']);raw_before=[r.copy() for r,_ in collision.layers]
        chosen,route,_=select_forward_route(options,actual,collision,target_slack=.1,planning_seconds=30.)
        self.assertEqual(route.method,'checked_roadmap_to_admitted_stance')
        self.assertEqual(route.start,actual)
        self.assertIn((chosen.goal,chosen.final_yaw),[(p.goal,p.final_yaw) for p in options])
        poses=[actual]+[(*phase.goal,phase.final_yaw) for phase in route.phases]
        self.assertTrue(all(collision.motion_free(a,b) for a,b in zip(poses,poses[1:])))
        for a,b in zip(poses,poses[1:]):
            dx,dy=b[0]-a[0],b[1]-a[1]
            if math.hypot(dx,dy)>.002:
                self.assertGreater(dx*math.cos(a[2])+dy*math.sin(a[2]),0.)
                self.assertLess(abs(angle(math.atan2(dy,dx)-a[2])),1e-6)
        self.assertTrue(all(np.array_equal(before,after) for before,(after,_) in zip(raw_before,collision.layers)))

    def test_floor_projection_cannot_choose_closer_pose_across_wall(self):
        import numpy as np
        from dataclasses import replace
        from manipulation_agent.executors.point_route_projection import prepare_point_projection
        if not prepare_point_projection():self.skipTest('Requires optional SciPy graph support')
        raw=np.ones((81,81),dtype='uint8');raw[:,40]=0
        footprint=[[-.15,-.1],[.15,-.1],[.15,.1],[-.15,.1]]
        collision=self.checker(raw,footprint)
        start=(0.,0.,0.)
        candidate=replace(plan([start[:2],(.5,0.)]),target=(2.,0.),score=1.5)
        selected,route,details=select_forward_route([candidate],start,collision,project_point=True)
        self.assertEqual(details['search'],'forward_reachable_point_projection')
        self.assertLess(selected.goal[0],1.)
        self.assertLess(selected.score,candidate.score)
        fresh=self.checker(raw,footprint);current=start
        for phase in route.phases:
            for xy in phase.points:
                destination=(*xy,phase.final_yaw)
                self.assertTrue(fresh.motion_free(current,destination))
                current=destination
        self.assertIsNotNone(local_exit_certificate(current,fresh))

    def test_relaxation_retains_passage_between_coarse_headings(self):
        import numpy as np
        yaw = .317
        rows, cols = np.indices((81,81))
        x, y = -1.+cols*.05, -1.+rows*.05
        raw = (np.abs(-math.sin(yaw)*x+math.cos(yaw)*y) < .14).astype('uint8')
        checker = self.checker(raw,[[-.45,-.035],[.45,-.035],[.45,.035],[-.45,.035]])
        self.assertTrue(checker.motion_free((0.,0.,yaw),(0.,0.,yaw)))
        self.assertTrue(all(not checker.motion_free((0.,0.,h*math.pi/4),(0.,0.,h*math.pi/4))
                            for h in range(8)))
        relaxed = checker.optimistic_grid()
        self.assertTrue(relaxed.navigable(relaxed.cell((0.,0.))))
        self.assertFalse(relaxed.navigable(relaxed.cell((0.,.5))))

    def test_offset_payload_does_not_block_its_free_base_in_relaxation(self):
        import numpy as np
        raw = np.ones((81,81),dtype='uint8')
        raw[20,20] = 0
        checker = self.checker(raw,[[.7,-.05],[.9,-.05],[.9,.05],[.7,.05]])
        self.assertTrue(checker.motion_free((0.,0.,0.),(0.,0.,0.)))
        self.assertTrue(checker.optimistic_grid().navigable(checker.grid.cell((0.,0.))))

    def test_segment_region_matches_full_map_at_edges_and_obstacle_halos(self):
        import numpy as np
        raw=np.ones((81,81),dtype='uint8')
        raw[30:45,30]=0;raw[55,20:60]=0;raw[15:18,48:52]=0
        footprint=[[-.12,-.08],[.23,-.08],[.23,.08],[-.12,.08]]
        checker=self.checker(raw,footprint)
        points=[((.4,.4),(.4,1.4)),((.425,.425),(.425,1.425)),
                ((.05,.05),(.45,.45)),((-.95,-.95),(-.8,-.8)),
                ((.2,.5),(.2,1.1)),((1.,1.),(1.8,1.8))]
        for bounded in (False,True):
            subject=checker.local_window((.5,.5),.7) if bounded else checker
            for yaw in (0.,.113,-.37,math.pi/2):
                for a,b in points:
                    with self.subTest(bounded=bounded,yaw=yaw,a=a,b=b):
                        subject.grids.clear()
                        got=subject.motion_free((*a,yaw),(*b,yaw))
                        reference=subject._sweep_grid(subject._key(yaw,yaw)).segment_free(a,b)
                        self.assertEqual(got,reference)

    def test_long_sweep_contains_every_short_sweep_including_wraparound(self):
        import numpy as np
        checker=self.checker(np.ones((81,81),dtype='uint8'),
                             [[-.4006,.0081],[-.1727,-.3071],[.3115,.2330],[.01,.3521]])
        for lo,hi in ((-125.2,53.5),(170.,250.),(-20.,80.)):
            full=checker._kernels(checker._key(math.radians(lo),math.radians(hi)))[0]
            for angle in range(math.ceil(lo)+1,math.floor(hi)-2):
                step=checker._kernels(checker._key(math.radians(angle),math.radians(angle+2)))[0]
                self.assertFalse(np.any((step!=0)&(full==0)),(lo,hi,angle))

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

    def test_local_window_preserves_collision_halo_and_global_grid_coordinates(self):
        import numpy as np
        from manipulation_agent.executors.local_manipulation import LocalCollision
        raw=np.ones((241,241),dtype='uint8')
        # Obstacle beyond the allowed base-centre disk still intersects a long
        # payload near its boundary; cropping must retain this outside halo.
        raw[80:123,131]=0
        checker=self.checker(raw,[[-.7,-.08],[.7,-.08],[.7,.08],[-.7,.08]])
        origin=(4.,4.,0.);local=LocalCollision(checker,origin,1.)
        self.assertEqual(local.grid.origin,checker.grid.origin)
        for x,y in [(4.,4.),(4.95,4.),(4.,4.9),(3.1,4.)]:
            for yaw in (0.,.12,math.pi/4,math.pi/2):
                a=(4.,4.,yaw);b=(x,y,yaw)
                self.assertEqual(local.motion_free(a,b),checker.motion_free(a,b),(a,b))
                self.assertEqual(local.motion_free(b,(*b[:2],yaw+.3)),
                                 checker.motion_free(b,(*b[:2],yaw+.3)))
        self.assertFalse(local.motion_free((4.95,4.,0.),(4.95,4.,0.)))
        self.assertTrue(local.motion_free((4.95,4.,math.pi/2),(4.95,4.,math.pi/2)))
        # A local grid cache must never restrict later macro navigation.
        self.assertTrue(checker.motion_free((8.,8.,0.),(9.,8.,0.)))

    def test_local_heading_erosion_is_bounded_and_cancellation_stays_live(self):
        import cv2
        import numpy as np
        from unittest.mock import patch
        from manipulation_agent.executors.local_manipulation import LocalCollision
        checker=self.checker(np.ones((401,401),dtype='uint8'),
                             [[-.35,-.1],[.35,-.1],[.35,.1],[-.35,.1]])
        local=LocalCollision(checker,(5.,5.,0.),2.)
        sizes=[];erode=cv2.erode
        def measured(raw,*args,**kwargs):
            sizes.append(raw.shape);return erode(raw,*args,**kwargs)
        with patch.object(cv2,'erode',measured):
            self.assertTrue(local.optimistic_grid().navigable(local.grid.cell((5.,5.))))
        self.assertTrue(sizes);self.assertTrue(all(max(shape)<120 for shape in sizes))
        def cancelled():raise RuntimeError('cancelled')
        checker.check_cancelled=cancelled
        with self.assertRaisesRegex(RuntimeError,'cancelled'):
            local.motion_free((5.,5.,0.),(5.1,5.,0.))


if __name__=='__main__':unittest.main()
