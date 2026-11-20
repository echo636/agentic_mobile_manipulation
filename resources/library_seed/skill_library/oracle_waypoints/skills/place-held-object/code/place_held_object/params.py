"""Supported controls; these values have model demonstrations, not code verification."""
SETTLE_STEPS = 90  # control steps; demonstrated 90 and 120; stable flag checks last 10 only
CLEARANCE_M = .08  # CALIBRATE: inspect actual container mouth/handle and carried-object extent
PATH_SPACING_M = .12  # maximum translational subdivision; not collision avoidance
MAX_TRANSFER_M = .60  # conservative code domain, not a demonstrated generalization range
