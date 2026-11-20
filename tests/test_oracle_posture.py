import math
import unittest
from manipulation_agent.executors.oracle_posture import body_posture_metrics


class MeasuredBodyPostureTests(unittest.TestCase):
    def test_yaw_does_not_count_as_body_lean(self):
        h=math.sqrt(.5)
        result=body_posture_metrics([1,2,.1],[1.1,2.2,1.3],[0,0,h,h])
        self.assertAlmostEqual(result["upper_body_tilt_deg"],0)
        self.assertAlmostEqual(result["upper_body_height_above_base_m"],1.2)

    def test_pitch_and_roll_are_measured_against_gravity(self):
        for q in ([math.sin(.2),0,0,math.cos(.2)],[0,math.sin(.2),0,math.cos(.2)]):
            self.assertAlmostEqual(body_posture_metrics([0,0,0],[0,0,1],q)["upper_body_tilt_deg"],math.degrees(.4))

    def test_equivalent_quaternion_sign_and_norm_do_not_change_tilt(self):
        q=[0,math.sin(.1),0,math.cos(.1)]
        a=body_posture_metrics([0,0,0],[0,0,1],q)
        b=body_posture_metrics([0,0,0],[0,0,1],[-3*v for v in q])
        self.assertAlmostEqual(a["upper_body_tilt_deg"],b["upper_body_tilt_deg"])

    def test_zero_or_nonfinite_orientation_is_rejected(self):
        for q in ([0,0,0,0],[0,float("nan"),0,1]):
            with self.assertRaises(ValueError):
                body_posture_metrics([0,0,0],[0,0,1],q)
