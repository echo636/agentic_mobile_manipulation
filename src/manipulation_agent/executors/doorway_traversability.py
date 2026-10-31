"""Apply a scene's supplied open-door traversability only at an open doorway."""
from __future__ import annotations

import numpy as np


def merge_open_door_cells(closed_map, open_map, *, center_xy, resolution, radius_m=1.0):
    """Add supplied open-door cells inside one door's local footprint.

    The maps have already been resized and thresholded like OmniGibson's
    TraversableMap. All nonlocal cells and all originally free cells remain
    unchanged. Erosion and movable-obstacle masking follow this merge.
    """
    closed = np.asarray(closed_map)
    opened = np.asarray(open_map)
    if closed.ndim != 2 or closed.shape != opened.shape:
        raise ValueError('Door traversability maps must have equal 2D dimensions')
    if resolution <= 0 or radius_m <= 0:
        raise ValueError('Positive map resolution and door radius required')
    height, width = closed.shape
    xs = (np.arange(width) - width / 2) * resolution
    ys = (np.arange(height) - height / 2) * resolution
    local = (xs[None, :] - center_xy[0]) ** 2 + (ys[:, None] - center_xy[1]) ** 2 <= radius_m ** 2
    additions = local & (closed == 0) & (opened != 0)
    merged = closed.copy()
    merged[additions] = opened[additions]
    return merged, int(np.count_nonzero(additions))
