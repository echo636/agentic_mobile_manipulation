# Stable elevated free-object grasp

Choose a reachable station before closing. Read the initial object's world height
and, if an upright finish is required, the measured torso_link4 body posture as the
standing reference. A level chest can still be a deep crouch: check both tilt and
height above base. Commanded trunk joint values alone are insufficient.

Use the object's current dimensions, finger geometry and wrist RGB to choose an
orientation and collision-clear pregrasp. Reach it in a short call, inspect wrist
alignment/occlusion, then author a short contact and verification-lift path. Use
`grasp_kind=free_object` only for an unanchored object. Check actual finger contact,
`held_object`, native retention, achieved WORLD lift and relative drift. Closing or
successful IK is not a grasp. Native assisted retention is not friction-only grasping.

If the final body posture must change while held, use small native arm/trunk motions,
inspect clearance and recompute geometry after each, and verify the object remains
raised. Do not invoke navigation with an object held to claim native carrying.
Finish only after the requested lift, retention and body posture hold together for
the declared settling interval. Do not release if the requested endpoint is holding.

Demonstrated bottle endpoint: world lift >=0.05 m, torso tilt <=5 degrees, torso
height >= initial standing height minus 0.08 m, all true for the last 60 control
steps (2 seconds at 30 Hz). These are that experiment's engineering criteria; derive
new acceptance thresholds from the next task instead of treating them as universal.
