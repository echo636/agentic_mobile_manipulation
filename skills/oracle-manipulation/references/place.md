# Native pick and place

Choose a reachable station for both source and destination before grasping. Nearby
objects can still lie outside the arm's usable workspace because of orientation,
body posture or joint limits. If several joints saturate during approach, withdraw
and change the empty robot's station; a deeper crouch alone may not solve it.

Use a short pregrasp and active wrist image, then author grasp approach and small
verification lift paths. Require native retention on the intended object/link and
actual world lift. Transport through native arm/torso waypoints with gripper=hold.
After torso motion, refresh hand/object geometry. Never substitute held-object
navigation for native transport.

Plan clearance for the carried object as well as the fingers. Lift the object above
the rim before translating over the opening, then lower to the chosen release pose.
Account for the measured hand-to-object offset. A container's outer AABB does not
certify an opening; inspect the mouth and handle in current RGB and link geometry.

Supply placement and retreat paths to `place`. Read `relation_satisfied`, `released`,
`release_evidence`, retreat tracking and stability separately. A release or an EEF
inside the container does not prove the object is inside. A failed path may leave
it retained at an intermediate pose; inspect before issuing another action.

Allow initial settling before the required stable interval. In this executor,
`stability.samples` counts all requested samples, but `stable` and
`position_drift_m` inspect only the last 10. Do not call that a measurement of the
entire interval. For strict 60-step position stability, independently inspect the
last 60 consecutive target-pose trace rows, disengagement and final geometry.
The demonstrated place requested 90 settling steps and passed the independent
last-60 position check. This measures position stability; it does not certify
whole-arm collision freedom or every-step native Inside predicates.

Demonstrated source: `oracle_transfer_can_placement_20261029_201056_dca3d1`.
After failed approaches and an empty station change, one assisted grasp and one
place succeeded. Native Inside/release/retreat passed; final 60 steps had zero
position drift. Full six-can task was 1/6, not complete. See evidence.md.
