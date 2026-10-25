"""Use metric radii consistently in offline initial-placement map checks.

OpenCV kernel dimensions are diameters. The native implementation uses a
radius as both its erosion and dilation width, shrinking effective arm reach
and making even-sized windows asymmetric. This scope fixes the map arithmetic;
native height, floor, articulated-target and bounds checks still decide poses.
These static maps are only a construction prefilter, not a collision-free path
or an inverse-kinematics certificate.
"""
from contextlib import contextmanager
import math


def metric_reachability_maps(floor, resolution, body_radius, arm_length):
    import cv2
    import numpy as np

    if resolution <= 0 or body_radius < 0 or arm_length < 0:
        raise ValueError('Invalid reachability map distances')
    radius = int(math.ceil(body_radius / resolution))
    arm = int(math.ceil(arm_length / resolution))
    floor = np.asarray(floor, dtype=np.uint8)
    # Keep the native square-map policy, with centered odd-size kernels.
    standing = cv2.erode(floor, np.ones((2 * radius + 1,) * 2, np.uint8),
                         borderType=cv2.BORDER_CONSTANT, borderValue=0)
    reach = cv2.dilate(standing, np.ones((2 * (radius + arm) + 1,) * 2, np.uint8),
                       borderType=cv2.BORDER_CONSTANT, borderValue=0)
    return standing, reach


@contextmanager
def metric_initial_reachability(record):
    import numpy as np
    import torch
    from omnigibson.utils import object_state_utils as states

    original_context = states.get_reachability_sampling_context
    original_check = states.is_pose_reachable_for_predicate

    def context(*args, **kwargs):
        result = original_context(*args, **kwargs)
        if result is None:
            return None
        target = kwargs.get('objB', args[0] if args else None)
        trav = result['trav_map']
        robot = target.scene.robots[0] if target.scene.robots else None
        radius = (float(torch.norm(robot.reset_joint_pos_aabb_extent[:2])) / 2. + .2
                  if robot is not None else trav.default_erosion_radius)
        arm = float(states.m.ARM_LENGTH_XY)
        standing, reachable = metric_reachability_maps(
            trav.floor_map[0].cpu().numpy(), trav.map_resolution, radius, arm)
        return dict(result, eroded_trav_map=torch.from_numpy(standing),
                    reachability_map=torch.from_numpy(reachable),
                    _mas_original_context=result, _mas_body_radius=radius, _mas_arm_length=arm)

    def check(pos, objB, predicate, reachability_context):
        accepted = bool(original_check(pos, objB, predicate, reachability_context))
        c = reachability_context
        if c is not None and '_mas_original_context' in c:
            previous = bool(original_check(pos, objB, predicate, c['_mas_original_context']))
            if previous != accepted:
                xy = c['trav_map'].world_to_map(pos[:2]).cpu().numpy()
                free = np.argwhere(c['eroded_trav_map'].cpu().numpy() == 255)
                delta = (free - xy) * c['trav_map'].map_resolution
                nearest = (float(np.linalg.norm(delta, axis=1).min()) if len(free) else None)
                record({'status': 'initial_reachability_metric_change', 'target': objB.name,
                        'relation': predicate, 'position': pos.tolist(), 'map_cell': xy.tolist(),
                        'original_accepted': previous, 'accepted': accepted,
                        'resolution_m': c['trav_map'].map_resolution,
                        'body_radius_m': c['_mas_body_radius'], 'arm_length_m': c['_mas_arm_length'],
                        'nearest_standing_cell_distance_m': nearest,
                        'policy': 'centered metric kernels; native height/floor/bounds checks retained'})
        return accepted

    states.get_reachability_sampling_context = context
    states.is_pose_reachable_for_predicate = check
    try:
        yield
    finally:
        states.get_reachability_sampling_context = original_context
        states.is_pose_reachable_for_predicate = original_check
