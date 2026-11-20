"""Hardware calibration and explicit draft acceptance choices, not safety limits."""

# R1Pro asset signature only. Operational limits always come from the observation.
R1PRO_LOWER_RAD = (-1.1345, -2.7925, -1.8326, -3.0543)
R1PRO_UPPER_RAD = (1.8326, 2.5307, 1.5708, 3.0543)
LIMIT_SIGNATURE_TOLERANCE_RAD = .001

# Engineering choice: excludes R2's near-limit [1.8, -2.75, -.95] candidate,
# whose q1/q2 margins were .0326/.0425 rad and actual tilt was 3.242 degrees.
# The well-tracked [1.5, -2.6, -1.1] candidate retains at least .1925 rad.
JOINT_LIMIT_MARGIN_RAD = .06

# Draft acceptance criteria, stricter than move_joints' .10 rad reached check.
# R2 low-upright measured <.01 degrees and <5e-6 rad joint error; these wider
# thresholds tolerate measurement/tracking variation but need real validation.
MAX_JOINT_ERROR_RAD = .03
MAX_UPPER_BODY_TILT_DEG = 2.0

# Engineering no-op threshold: avoid a near-zero joint move only when measured
# uprightness and any caller height interval already pass too. Not a safety bound.
ALREADY_SATISFIED_JOINT_ERROR_RAD = .01
