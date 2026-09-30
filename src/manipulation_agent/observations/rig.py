"""Four fixed camera extrinsics in the robot base frame (X forward, Z up)."""
import math

DIRECTIONS = ('front', 'back', 'left', 'right')
YAW_DEGREES = {'front': 0, 'back': 180, 'left': 90, 'right': -90}


def camera_mount(direction, height, radius=0.35, pitch_degrees=20):
    yaw, pitch = math.radians(YAW_DEGREES[direction]), math.radians(pitch_degrees)
    c, s, cp, sp = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch)
    right = (s, -c, 0)
    up = (c*sp, s*sp, cp)
    forward = (c*cp, s*cp, -sp)
    # USD cameras see along local -Z; columns are right, up and backward.
    rotation = [[right[i], up[i], -forward[i]] for i in range(3)]
    return [radius*c, radius*s, height], rotation
