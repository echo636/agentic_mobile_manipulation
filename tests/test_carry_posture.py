"""Carry packing geometry and real adapter sequencing; no simulator acceptance."""
from contextlib import nullcontext
import importlib.util
import math
import sys
from types import SimpleNamespace, ModuleType
import unittest
from unittest.mock import patch
from manipulation_agent.executors.carry_posture import compact_translation, translation_samples


class CarryRouteTests(unittest.TestCase):
    def test_whole_assembly_size_and_swept_route_are_required(self):
        origin=(1.3,0.,.2);base=(0.,0.,0.,0.)
        bounds=[((1.25,-.05,.15),(1.35,.05,.25))]
        checks=[]
        route,status=compact_translation(origin,base,bounds,lambda a,b:checks.append((a,b)) or True)
        self.assertEqual(status,'planned');self.assertTrue(checks)
        self.assertLess(math.dist(route[-1][:2],base[:2]),.7)
        self.assertGreaterEqual(route[-1][2]-.05,.65)
        samples=list(translation_samples(route,.01))
        self.assertTrue(all(math.dist(a,b)<=.010001 for a,b in zip([origin,*samples],samples)))
        self.assertLess(math.dist(samples[-1],route[-1]),1e-12)
        route,status=compact_translation(origin,base,[*bounds,((.2,-.1,.2),(2.4,.1,.4))],lambda a,b:True)
        self.assertIsNone(route);self.assertEqual(status,'payload_too_large')

    def test_full_size_table_moves_around_robot_only_through_clear_sweeps(self):
        origin=(0.,-1.6,.84);base=(0.,0.,0.,0.)
        bounds=[((-.41,-2.27,.83),(.41,-.93,.85))]
        # Conservative body expanded by the complete tabletop radius.
        reserve=.42+math.hypot(.41,.67)
        checks=[]
        def clear(a,b):
            checks.append((a,b))
            vx,vy=b[0]-a[0],b[1]-a[1]
            d=vx*vx+vy*vy
            t=max(0.,min(1.,-(a[0]*vx+a[1]*vy)/d)) if d else 0.
            return math.hypot(a[0]+t*vx,a[1]+t*vy)>reserve
        route,status=compact_translation(origin,base,bounds,clear)
        self.assertEqual(status,'planned')
        self.assertTrue(checks)
        self.assertFalse(clear(origin,route[-1]))
        self.assertTrue(all(clear(a,b) for a,b in zip(route,route[1:])))
        self.assertAlmostEqual(route[-1][1],0.)
        self.assertLessEqual(math.dist(route[-1][:2],base[:2]),1.4)
        self.assertGreaterEqual(route[-1][2]-.01,.95)
        self.assertLessEqual(sum(math.dist(a,b) for a,b in zip(route,route[1:])),3.)
        blocked,status=compact_translation(origin,base,bounds,lambda a,b:False)
        self.assertIsNone(blocked);self.assertEqual(status,'blocked')

    def test_wide_payload_uses_lower_checked_pose_under_a_low_ceiling(self):
        origin=(1.6,0.,.84);base=(0.,0.,0.,0.)
        bounds=[((1.19,-.67,.83),(2.01,.67,.85))]
        def clear(a,b):
            return max(a[2],b[2])+.01 < .91
        route,status=compact_translation(origin,base,bounds,clear)
        self.assertEqual(status,'planned')
        self.assertTrue(all(clear(a,b) for a,b in zip(route,route[1:])))
        self.assertAlmostEqual(route[-1][2],.84)

    def test_obstacle_in_every_swept_route_preserves_original_posture(self):
        route,status=compact_translation((1.3,0.,.2),(0.,0.,0.,0.),
            [((1.25,-.05,.15),(1.35,.05,.25))],lambda a,b:False)
        self.assertIsNone(route);self.assertEqual(status,'blocked')

    def test_side_payload_moves_around_body_to_reduce_loaded_width(self):
        origin=(0.,.65,1.);base=(0.,0.,0.,0.)
        bounds=[((-.08,.57,.9),(.08,.73,1.1))]
        def clear(a,b):
            # Expanded circular body: endpoints alone are insufficient.
            vx,vy=b[0]-a[0],b[1]-a[1]
            t=max(0.,min(1.,-(a[0]*vx+a[1]*vy)/(vx*vx+vy*vy))) if vx*vx+vy*vy else 0.
            return math.hypot(a[0]+t*vx,a[1]+t*vy)>.5
        route,status=compact_translation(origin,base,bounds,clear)
        self.assertEqual(status,'planned')
        self.assertFalse(clear(origin,route[-1]))
        self.assertTrue(all(clear(a,b) for a,b in zip(route,route[1:])))
        self.assertAlmostEqual(route[-1][1],0.)
        self.assertGreater(route[-1][0],.55)
        self.assertLessEqual(sum(math.dist(a,b) for a,b in zip(route,route[1:])),2.)

    def test_blocked_front_keeps_safe_side_pose_and_propagates_cancellation(self):
        origin=(0.,.6,1.);base=(0.,0.,0.,0.)
        bounds=[((-.08,.52,.9),(.08,.68,1.1))]
        route,status=compact_translation(origin,base,bounds,lambda a,b:b[0]<.1)
        self.assertEqual(route,());self.assertEqual(status,'already_compact')
        def cancel():raise TimeoutError('cancelled')
        with self.assertRaises(TimeoutError):
            compact_translation(origin,base,bounds,lambda a,b:True,check=cancel)
