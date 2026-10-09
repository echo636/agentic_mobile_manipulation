import math
import unittest

from manipulation_agent.observations.rig import camera_mount, centered_head_height, visible_rig_rays, DIRECTIONS


class CenteredRigTests(unittest.TestCase):
    def test_forward_camera_keeps_near_container_inside_rgb_frame(self):
        target=(1.25,0.,.5)
        shallow=visible_rig_rays((0.,0.),0.,0.,1.81,target,
                                  margin=.18,radius=.35,pitch_degrees=20)
        configured=visible_rig_rays((0.,0.),0.,0.,1.81,target,
                                     margin=.18,radius=.35,pitch_degrees=35)
        self.assertFalse(shallow)
        self.assertTrue(configured)
        self.assertLessEqual(configured[0][2][1],.82)

    def test_visual_head_and_shoulders_stay_below_lower_image_band(self):
        # A visual head above its collision AABB and wide lower shoulders:
        # using collision_top + .05 would place the old camera inside the head.
        base = (4.9, 3.8, .005)
        points = [(base[0]+x, base[1]+y, base[2]+z)
                  for x, y, z in ((.2, 0, 1.7), (-.2, 0, 1.7), (0, .2, 1.7),
                                  (0, -.2, 1.7), (.5, .3, .9), (-.5, -.3, .9),
                                  (.4, -.3, .4), (-.4, .3, .4))]
        height = centered_head_height(points, base)
        self.assertGreater(height, 1.7)
        visible = 0
        for view in DIRECTIONS:
            offset, rotation = camera_mount(view, height, radius=0)
            origin = [a+b for a, b in zip(base, offset)]
            for point in points:
                delta = [p-o for p, o in zip(point, origin)]
                local = [sum(rotation[i][j]*delta[i] for i in range(3)) for j in range(3)]
                depth = -local[2]
                if depth <= 0:
                    continue
                u, v = .5+.5*local[0]/depth, .5-.5*local[1]/depth
                if 0 <= u <= 1 and 0 <= v <= 1:
                    visible += 1
                    self.assertGreaterEqual(v, .8)
        self.assertGreater(visible, 0)

    def test_height_is_translation_and_yaw_invariant(self):
        points = [(.3, .1, 1.6), (-.4, .2, .8), (0, 0, 1.7)]
        height = centered_head_height(points, (0, 0, 0))
        angle = .73
        transformed = [(2+x*math.cos(angle)-y*math.sin(angle),
                        -3+x*math.sin(angle)+y*math.cos(angle), 4+z) for x, y, z in points]
        self.assertAlmostEqual(height, centered_head_height(transformed, (2, -3, 4)))

    def test_four_independent_directions_share_origin(self):
        mounts = [camera_mount(view, 2., radius=0) for view in DIRECTIONS]
        self.assertTrue(all(offset == [0., 0., 2.] for offset, _ in mounts))
        self.assertEqual(len({tuple(row[2] for row in basis) for _, basis in mounts}), 4)


if __name__ == '__main__':
    unittest.main()
