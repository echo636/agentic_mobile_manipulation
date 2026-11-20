"""Model-demonstrated arc scales; compiled code remains draft until robot-tested."""
ARC_RAD = .10  # model demonstrations used .06–.12 rad; reobserve between segments
ARC_POINT_RAD = .04
JOINT_TOLERANCE_RAD = .01
MAX_OPENING_RAD = .80  # supported per invocation; no claim of tested generalization
MAX_SEGMENTS = 16  # bounded subroutine; not an episode limit
RETREAT_M = .12  # CALIBRATE: input retreat direction must clear the current door/panel
HOLD_STEPS = 60
