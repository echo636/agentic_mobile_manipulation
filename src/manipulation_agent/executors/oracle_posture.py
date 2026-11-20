"""Measured upper-body posture metrics, with no simulator dependency."""
import math


def body_posture_metrics(base_position_world, upper_position_world, upper_quaternion_xyzw):
    values = [*base_position_world, *upper_position_world, *upper_quaternion_xyzw]
    if (len(base_position_world) != 3 or len(upper_position_world) != 3
            or len(upper_quaternion_xyzw) != 4 or not all(math.isfinite(x) for x in values)):
        raise ValueError("Posture requires finite measured world poses")
    x,y,z,w = upper_quaternion_xyzw
    norm = x*x+y*y+z*z+w*w
    if norm <= 1e-12:
        raise ValueError("Invalid upper-body quaternion")
    # torso_link4's authored +Z is the upper-body vertical axis at zero trunk pitch.
    cosine = 1 - 2*(x*x+y*y)/norm
    return {"upper_body_tilt_deg": math.degrees(math.acos(max(-1.,min(1.,cosine)))),
            "upper_body_height_above_base_m": upper_position_world[2]-base_position_world[2]}
