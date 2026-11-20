---
name: behavior-collecting-aluminum-cans
description: "Plan the full six-can task with explicit native/hybrid transport, calibrated torso preparation and failure-driven station changes; full-task success remains unverified."
---

# collecting aluminum cans

Scope: oracle_waypoints; task=collecting_aluminum_cans; revision=3.
Past experience only. Recompute targets from the current observations; do not replay old coordinates or object IDs.

## Scope and result
Historical native one-can pick/place passed independent engineering review in three runs; the original six-can task remained incomplete. The new station-reuse full-task exploration ended officially 0/6, with 33 physical motor calls and 15 failures. It never established retention. The following lessons retain that failure rather than presenting a full-task recipe as verified. R1Pro, public instance301/seed0, six-view RGB, Oracle geometry, official IK and native assisted retention are the demonstrated setting.

## Plan all six and choose the transport contract
Resolve all six task cans and the designated bucket from the current episode. Track each as pending, held, or released inside. Keep the bucket stationary and carry exactly one can at a time. Under initialize.control.transport_policy=native_only, do not navigate while holding: only local native transport is supported. Only an explicitly authorized hybrid_legacy_navigation episode permits the existing navigator to project one held object's pose. Label it ideal transport, refresh geometry/retention afterward, and do not carry a loaded bucket or two held objects.

## Reach preparation and station recovery
Check the source relative to the base footprint, shoulder reach and rim/furniture before moving the hand. A target trapped at a rear base corner plus bucket rim was not solved by repeated wrist-yaw and torso changes in the failed exploration. If different orientations still saturate the same shoulder, elbow or wrist limits, reject the station while empty and select a genuinely different approach using current surround RGB. Avoid exchanging many joint variants without a new geometric hypothesis. The failed run's later station was also not validated; do not copy its coordinates or yaw.

Use hardware posture knowledge instead of guessing independent torso signs. R1Pro's upright branch is q3=q1+q2; both positive and negative q3 can be correct. Calibrated historical candidates [q1,q2,q3]=[1.5,-2.6,-1.1] and [-.9,1.8,+.9] are hardware configurations, not collision-safe scene paths; choose yaw and height from current geometry. Read the upright-trunk-posture action experience. In oracle_compiled its draft program can derive q3, reject limit violations and skip an already-satisfied posture, but requires prepared empty hands and explicit allow_draft=true. The supplied raised bent-ready arm is not a guaranteed floor-grasp branch. Prepare in clear space, keep the other arm compact and inspect actual fingers/body posture after trunk changes.

## Grasp and local place
Compute EEF contact targets from the current finger-center offset and chosen normalized downward orientation: desired_eef=desired_contact-R(q)*finger_offset. Execute a short safe precontact, inspect the active wrist and refine. Native grasp must retain the intended can/link and visibly lift it. Historical successful grasp used native trunk adjustment; a fixed-trunk reach miss does not justify indefinitely repeating the same extended arm. If wrist alignment and clearance are established, consider a reasoned trunk-enabled native motion rather than requiring a fixed-trunk seed to do an impossible reach. Recheck actual torso and both hands afterward. Failed results can still change contacts, pose or retention; never automatically reopen to clear an error.

For a retained object, desired_eef=current_eef+desired_object-current_object maintains the measured offset when orientation is unchanged. Clear the whole can above the true bucket rim, move through the visible opening, release and retreat. The optional place-held-object draft batches local place/retreat/90-step settling but requires current object-to-release distance<=.60m; it does not solve a room-crossing trip or initial grasp. Do not enlarge that limit to hide a navigation problem. Account for already deposited cans and remeasure the bucket after any contact.

## Navigation and finish
Compact both arms for the corridor; the current navigator recomputes collision footprint from measured link geometry. Use current surround floor pixels and inspect actual target projection and endpoint, not just reached=true. A large target_projection_distance_m measures endpoint-to-clicked-target error, not route length; it does not by itself prove physical impassability. Try a reasoned intermediate floor station or a different route view rather than repeating the same projected destination. Do not use mirror reflections or wrist pixels for travel.

Require released and actual Inside separately; native stable covers only its last10 samples, while strict60-step stability needs independent passive-trace review. Existing place settling need not be followed by an automatic extra wait. Finish achieved only after all six are inside and both hands empty; the independent official evaluator decides task success. Preserve failures and distinguish a local program success from the complete task. Neither these lessons nor skill reads establish a speed or success-rate improvement.

## Recorded evidence

- oracle_transfer_can_placement_20261029_201056_dca3d1: task=collecting_aluminum_cans, instance=301, seed=0; official whole-task success=False; engineering success=True; claim=engineering_success; grasping=assisted; fixed-floor repairs=1.
- oracle_transfer_can_placement_skill_r1_20261029_223245_958875: task=collecting_aluminum_cans, instance=301, seed=0; official whole-task success=False; engineering success=True; claim=engineering_success; grasping=assisted; fixed-floor repairs=1.
- oracle_transfer_can_placement_skill_r2_20261029_234018_11de7b: task=collecting_aluminum_cans, instance=301, seed=0; official whole-task success=False; engineering success=True; claim=engineering_success; grasping=assisted; fixed-floor repairs=1.
- oracle_transfer_can_full_station_reuse_20261030_151818: task=collecting_aluminum_cans, instance=301, seed=0; official whole-task success=False; engineering success=False; claim=partial_or_failure; grasping=assisted; fixed-floor repairs=1.
