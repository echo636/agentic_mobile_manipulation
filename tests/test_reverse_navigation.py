"""A doorway must allow retreat with identical collision and follower checks."""
import math
import unittest
from manipulation_agent.executors.forward_navigation import plan_forward_route,route_from_poses,angle
from manipulation_agent.executors.gt_navigation import GridMap,NavigationPlan,GreedyGridFollower

class Doorway:
    grid=GridMap(101,101,.05,(-2.,-2.),bytes([1])*101**2)
    def motion_free(self,a,b):
        for i in range(101):
            f=i/100;x=a[0]+(b[0]-a[0])*f;y=a[1]+(b[1]-a[1])*f
            yaw=a[2]+angle(b[2]-a[2])*f
            if x<-.81 or x>1.01 or abs(y)>.5:return False
            if x<-.1 and (abs(y)>.04 or abs(angle(yaw-math.pi))>.01):return False
        return True

class ReverseTests(unittest.TestCase):
    def test_back_out_of_doorway_and_execute_every_checked_phase(self):
        c=Doorway();start=(-.8,0.,math.pi);goal=(.8,.3)
        p=NavigationPlan((start[:2],goal),goal,goal,math.pi,2.,0.,1,1,0.,0)
        route=plan_forward_route(p,start,c,max_expansions=30)
        self.assertEqual(route.method,'checked_reverse_staging')
        self.assertTrue(any(p.motion_mode=='reverse' for p in route.phases))
        actual=start
        for phase in route.phases:
            follower=GreedyGridFollower(c.grid,phase,.02,forward_only=True,motion_check=c.motion_free)
            for _ in range(1000):
                command=follower.next_pose(actual)
                if command is None:break
                self.assertTrue(c.motion_free(actual,command));actual=command
            else:self.fail('Follower did not finish')
        self.assertLess(math.dist(actual[:2],goal),.003)

    def test_reconstruction_does_not_erase_a_direction_change(self):
        c=Doorway();poses=[(0.,0.,math.pi),(.5,0.,math.pi),(0.,0.,math.pi)]
        p=NavigationPlan(tuple(q[:2] for q in poses),(0.,0.),(0.,0.),math.pi,1.,0.,1,1,0.,0)
        from unittest.mock import patch
        with patch('manipulation_agent.executors.forward_navigation.simplify_forward_poses',return_value=(poses,0)):
            route=route_from_poses(p,poses,c)
        self.assertEqual([q.motion_mode for q in route.phases],['reverse','forward'])

if __name__=='__main__':unittest.main()
