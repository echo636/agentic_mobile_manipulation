"""Bounded kinematic base trajectory, executed live (never a video interpolation)."""
import math


def trajectory(points, start_yaw, end_yaw, dt, speed=.5, turn_speed=math.pi/3):
    if dt <= 0 or speed <= 0 or turn_speed <= 0: raise ValueError('Positive rates required')
    points = [tuple(map(float,p)) for p in points]
    if not points or any(len(p)!=2 or not all(math.isfinite(v) for v in p) for p in points):
        raise ValueError('Finite XY path required')
    lengths = [math.dist(a,b) for a,b in zip(points,points[1:])]
    distance = sum(lengths)
    delta = math.atan2(math.sin(end_yaw-start_yaw), math.cos(end_yaw-start_yaw))
    count = max(1, math.ceil(max(distance/speed,abs(delta)/turn_speed)/dt))
    poses = []; segment = 0; accumulated = 0.
    for i in range(1,count+1):
        travel = distance*i/count
        while segment < len(lengths)-1 and accumulated+lengths[segment] < travel:
            accumulated += lengths[segment]; segment += 1
        if not lengths: xy = points[0]
        else:
            fraction = min(1.,max(0.,(travel-accumulated)/lengths[segment])) if lengths[segment] else 1.
            xy = tuple(a+(b-a)*fraction for a,b in zip(points[segment],points[segment+1]))
        poses.append((*xy,start_yaw+delta*i/count))
    return poses
