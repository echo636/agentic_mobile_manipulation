# Custom task fixture corrections

## Study hanging anchors (2026-10-31)

The two added nails at height 1.70 m overlapped the original fixed wall clock.
Native rays from the room hit the clock for 6/9 samples on the first nail and
9/9 on the second. Their original orientation also aligned a mounted poster
inside the wall; native contact could then break its attachment.

Place the same nails at 1.30 m and rotate their original orientation by 180
degrees around world Z, preserving the poster's vertical orientation. The
posters now hang below the clock, in front of the wall, above the desktop.
Both anchors pass 9/9 visibility rays and both original posters remain attached
after normal physics. No collision filtering or joint-strength changes are used.

Native evidence: operations/long_horizon_full_v2_20261022/mas_study_nails_r539,
mas_study_nails_r540, and mas_study_nails_r546. These are component diagnostics,
not autonomous task successes.

Preserve historical instance 301 and its results. New model attempts use
instance 302, fixture cohort `visible_wall_anchors_r541`, with the changed
scene and spec hashes. Instruction, requirements and evaluator are unchanged;
results must identify the fixture cohort.
