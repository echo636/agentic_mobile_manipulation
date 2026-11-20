# Hinged-door manipulation

Read the target link, movable ancestor joint, axis origin/direction, current angle
and limits. A door category or link AABB is not an exact handle grasp pose. The
microwave demonstrated here had no separate handle region; current wrist RGB,
calibration and finger offsets were needed to refine the contact point.

Prepare a bent arm in clear space, choose a station, then re-read geometry. Reach
a safe pre-contact pose in a short call and inspect wrist clearance. Approach the
protruding handle without driving the palm into the surrounding fixed panel.

Use `grasp_kind=articulated_handle`. Do not apply a free-object vertical lift to an
anchored link: provide a small joint-consistent verification path. The current
schema still requires `min_lift_m >= 0.01` even though that criterion is ignored
for articulated handles. Supply a valid field without adding an upward lift.

Two finger contacts can precede assisted retention. Native assisted handling also
requires its contact/raycast/time-window conditions. If retention is absent, read
the new wrist/contact state rather than declaring a grasp or repeatedly reopening.
In the demonstrated episode, a small clearance-aware adjustment with the gripper
still closed established native retention after the shallow grasp reported failure.
Derive any correction direction and clearance from the current episode.

Before rotation, require the intended `native_grasp.target_link`, not merely the
object ID. The demonstrated door used a native SphericalJoint constraint on its
movable link. Probe a short arc and verify measured joint progress and retention.
Generate all arc points yourself around the observed hinge: for axis rotation R,
`p_new = hinge + R * (p_old - hinge)`; rotate the EEF orientation consistently in
the same base frame, with normalized xyzw quaternions. Keep segments short and
recompute from fresh observations when torso or base geometry changes.

Use `rotate` with the observed joint ID and signed relative angle. Actual angle,
retention and parent drift matter independently of IK reached. Permit torso IK only
when its collateral motion is clear. Stop and revise after tracking/contact fails.
Open beyond the requested threshold with a sensible margin, release, retreat, then
wait and re-read the angle and contacts. Do not navigate while retaining the handle.

Demonstrated source: `oracle_transfer_microwave_door_20261029_194648_19c826`.
Short native arcs opened the door to about 0.655 rad. It was released and retreated;
60-step wait endpoints were both 0.6527785063 rad, with no retained object. These
are verified endpoint measurements. This simulator version did not record the
joint at every control step, so strict continuous 60-step opening remains
uncertified. Future experiments requiring that proof need passive per-step joint
logging before launch. Full popcorn cooking was not attempted or completed.
Drawer translation has no demonstrated success in these references.
