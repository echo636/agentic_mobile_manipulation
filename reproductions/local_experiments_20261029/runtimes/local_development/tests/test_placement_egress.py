"""Floor placement may occupy cells but must not cut the base's free region."""
import unittest
from manipulation_agent.executors.gt_navigation import (
    GridMap, mask_dynamic_obstacles, reachable_component, preserves_reachable_region,
)


class PlacementEgressTests(unittest.TestCase):
    def corridor(self):
        free=bytearray(11*41)
        for row in range(41):
            for col in range(3,8):free[row*11+col]=1
        return GridMap(11,41,.1,(0.,0.),bytes(free))

    def test_bag_sealing_corridor_is_rejected(self):
        before=self.corridor();start=(.5,.5)
        after,_=mask_dynamic_obstacles(before,[(.4,1.9,.6,2.1)],margin=.38,start=start)
        diagnostic={}
        self.assertFalse(preserves_reachable_region(before,after,start,diagnostics=diagnostic))
        self.assertGreater(diagnostic['disconnected_cells'],0)
        self.assertAlmostEqual(diagnostic['disconnected_area_m2'],
                               diagnostic['disconnected_cells']*.01)
        self.assertLessEqual(len(diagnostic['disconnected_sample']),20)

    def test_side_placement_preserves_exit(self):
        before=self.corridor();start=(.5,.5)
        after,_=mask_dynamic_obstacles(before,[(1.4,1.9,1.6,2.1)],margin=.38,start=start)
        self.assertTrue(preserves_reachable_region(before,after,start))

    def test_occupied_start_is_not_an_allowed_exit(self):
        before=self.corridor();free=bytearray(before.free);free[5*11+5]=0
        after=GridMap(before.width,before.height,before.resolution,before.origin,bytes(free))
        self.assertFalse(preserves_reachable_region(before,after,(.5,.5)))

    def test_cells_occupied_by_placement_do_not_require_preservation(self):
        before=GridMap(20,20,.1,(0.,0.),bytes([1])*400)
        after,_=mask_dynamic_obstacles(before,[(1.,1.,1.1,1.1)],margin=.2,start=(.3,.3))
        component=reachable_component(before,(.3,.3))
        self.assertTrue(preserves_reachable_region(before,after,(.3,.3),baseline_component=component))
