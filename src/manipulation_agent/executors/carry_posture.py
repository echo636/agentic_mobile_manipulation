"""Bounded translation of an already-held assembly into a compact carry pose.

This remains an ideal motor operation, not an arm IK solver. Every swept box
includes the robot in collision queries; only the payload and meta links are
ignored. A blocked packing motion preserves the successful grasp.
"""
import math
import time
from ..contracts import SkillError


def compact_translation(origin, base, bounds, clear, *, check=lambda: None):
    """Read-only route proposal. Bounds are each link's world AABB."""
    radius = max(math.hypot(x-origin[0], y-origin[1]) for lo,hi in bounds
                 for x in (lo[0],hi[0]) for y in (lo[1],hi[1]))
    reach = max(.55, .42+radius+.06)
    # Full-size desk components need roughly 1.27 m of centre standoff.
    # The former .95 m cutoff skipped every packing proposal for them,
    # preserving a long side offset even when a checked route was available.
    # Stay within the ordinary 1.4 m manipulation reach; the entire payload
    # and robot still participate in every continuous swept-volume check.
    if reach > 1.4:
        return None, 'payload_too_large'
    distance = math.dist(origin[:2], base[:2])
    direction = math.atan2(origin[1]-base[1], origin[0]-base[0])
    front_delta = math.atan2(math.sin(base[3]-direction), math.cos(base[3]-direction))
    if distance <= reach+.05 and abs(front_delta) <= math.radians(10):
        return (), 'already_compact'
    bottom = min(lo[2]-origin[2] for lo,hi in bounds)
    # Wide furniture parts otherwise skim ordinary worktops and assembly
    # supports after the short extraction lift. Prefer the existing higher
    # transport proposal, with the lower one retained for overhead obstacles.
    heights = (.95, .65) if reach > .95 else (.65, .95)
    # A short side offset can still make a loaded robot too wide for a door.
    # Prefer a front pose, including a checked polygonal route around the
    # body when a direct pull crosses it. The assembly never rotates here.
    for angle in (base[3], direction, base[3]+math.pi/2, base[3]-math.pi/2):
        if angle == direction and distance <= reach+.05:
            return (), 'already_compact'
        for height in heights:
            check()
            end = (base[0]+reach*math.cos(angle), base[1]+reach*math.sin(angle),
                   max(origin[2], base[2]+height-bottom))
            raised = (origin[0],origin[1],end[2])
            routes = [(origin,end),(origin,raised,end)]
            if angle == base[3] and abs(front_delta) > math.radians(10):
                count = math.ceil(abs(front_delta)/math.radians(15))
                arc = tuple((base[0]+reach*math.cos(direction+front_delta*i/count),
                             base[1]+reach*math.sin(direction+front_delta*i/count),end[2])
                            for i in range(count+1))
                routes.append((origin,raised,*arc))
            for route in routes:
                if sum(math.dist(a,b) for a,b in zip(route,route[1:])) > 3.:
                    continue
                if all(clear(a,b) for a,b in zip(route,route[1:])):
                    return route, 'planned'
    return None, 'blocked'


def translation_samples(route, step_m):
    for a,b in zip(route,route[1:]):
        count = max(1,math.ceil(math.dist(a,b)/step_m))
        for i in range(1,count+1):
            yield tuple(x+(y-x)*i/count for x,y in zip(a,b))
