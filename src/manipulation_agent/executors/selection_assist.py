"""Harness option (route A selection module, default off): conservative point snapping.

When the selected pixel lands on a surface that cannot take the requested action
(e.g. ``grasp`` on a counter, ``open`` on a wall), and exactly one object that can
take it is visible within ``SNAP_RADIUS_PX`` of the selected pixel, the executor uses
a point on that object instead and reports the adjustment publicly. Several nearby
candidates, or none, leave the selection unchanged.

The decision logic here is pure Python so it can be tested without a simulator.
The executor supplies the visible candidate pixels (depth back-projection inside the
candidate's bounds, then the normal grounding check at the chosen pixel).

Radius: 24 px on a 512 px image (scaled for other sizes). The rig cameras have
fx = fy = 256 px (90 degree field of view), so 24 px spans about 9 cm at 1 m and
about 14 cm at 1.5 m -- roughly one plate or apple width. A click "just beside"
an item snaps; a miss by a whole shelf height (about 150 px in the diagnosed
episodes) does not.
"""
from __future__ import annotations

SNAP_PRIMITIVES = frozenset({'grasp', 'open', 'close', 'toggle_on', 'toggle_off', 'cut'})
SNAP_RADIUS_PX = 24
SNAP_REFERENCE_WIDTH = 512
MAX_VERIFIED_CANDIDATES = 4


def snap_radius(width: int) -> int:
    return max(1, round(SNAP_RADIUS_PX * width / SNAP_REFERENCE_WIDTH))


def candidate_order(pixels):
    """Pixels to try for one candidate: the one nearest its visible centroid first,
    then the rest nearest to the selected pixel. ``pixels`` is [(x, y, distance)]."""
    if not pixels:
        return []
    cx = sum(p[0] for p in pixels) / len(pixels)
    cy = sum(p[1] for p in pixels) / len(pixels)
    central = min(pixels, key=lambda p: (p[0] - cx) ** 2 + (p[1] - cy) ** 2)
    rest = sorted((p for p in pixels if p is not central), key=lambda p: p[2])
    return [central, *rest]


def decide(verified):
    """``verified`` maps candidate key -> (x, y, distance_px) of a checked pixel.

    Returns ('adjusted', key) for exactly one candidate, ('multiple', n) for
    several, ('none', 0) for none.
    """
    if len(verified) == 1:
        return 'adjusted', next(iter(verified))
    if len(verified) > 1:
        return 'multiple', len(verified)
    return 'none', 0


def public_adjustment(status, *, image_ref, selected_point, adjusted_point=None, distance_px=None,
                      candidates=0, radius_px=SNAP_RADIUS_PX):
    """Model-visible record. No object names or categories (that is a separate switch)."""
    if status == 'adjusted':
        return {
            'applied': True, 'image_ref': image_ref,
            'selected_point': [round(float(v), 4) for v in selected_point],
            'adjusted_point': [round(float(v), 4) for v in adjusted_point],
            'distance_px': round(float(distance_px), 1), 'radius_px': radius_px,
            'message': ('Selection adjusted: your point was on a surface that does not support this action. '
                        f'Exactly one object that supports it was visible within {radius_px} px, so the executor '
                        'used a point on that object instead (adjusted_point). Check the adjusted-point crop and the '
                        'returned RGB to confirm it is the object you intended.'),
        }
    if status == 'multiple':
        return {
            'applied': False, 'image_ref': image_ref, 'candidates_nearby': int(candidates),
            'radius_px': radius_px,
            'message': (f'Selection not adjusted: {int(candidates)} different objects that support this action are '
                        f'visible within {radius_px} px of your point. Select a point on the intended object itself.'),
        }
    return None
