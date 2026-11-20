---
name: grasp-countertop-bottle
description: Reuse and iterate the RAX-style Oracle controller for one countertop wine bottle, including native assisted grasp, lift, torso restoration and steady holding.
---

# Grasp a bottle

This is a RAX `main(ctx)` controller for Robot Harness, not the older
`oracle_compiled` program interface. Read `spec.json` and `evidence.json` for the
exact code/runner identity, current validation status and failed attempts.

## Conditions

R1Pro, Oracle 3D, stock surround and wrist RGB, official OmniGibson IK, native
assisted retention. Both hands must start empty. Resolve the task binding
`wine_bottle.n.01_1` and its countertop in the current scene. The task is one
bottle grasp/lift/hold; this does not implement the full recycling task.

The controller includes existing ideal base navigation while empty. Its camera
projection calibration was inferred in the initial exploration and can fail in
other layouts or camera setups. No navigation is permitted after pickup.

## Reuse

Use `controller.py` unchanged as the starting code in a newly prepared RAX
workspace, for example `workspace/code/bottle/controller.py`. Preserve its
provenance outside the agent-visible workspace. Re-register and rehearse under
the new session's frozen runner; a different runner has a different bundle
identity. This entry is not callable through `execute_skill`.

The outer experiment session may stage this library code through the existing
workspace preparation workflow. The in-episode agent can read only its staged
workspace copy. Do not expose other episodes' coordinates or formal schedules
as controller inputs.

## Procedure and checks

Read current object, hand/finger, torso and relevant obstacle geometry. Convert
the bottle-neck contact center to an EEF pose using measured finger offsets.
Prepare arms only with clearance; the idle arm can hit the counter or sink when
the torso bends. The coded idle-arm posture is a candidate, not a general
collision-free posture.

The relative procedure is a side approach near the bottle neck, a short native
grasp/lift, local retraction toward the shoulder, restoration of the initial
standing torso posture, and a measured steady hold. Recompute poses after
navigation or torso changes. The code stops on failed or ambiguous checks;
inspect the returned actual state before choosing a recovery. Do not release a
retained bottle merely to clear an error.

Success requires the intended bottle retained in exactly one hand, actual world
lift at least 0.05 m, and a final continuous 60 control steps with EEF-relative
drift at most 0.03 m, torso tilt at most 5 degrees and torso height at least its
initial standing height minus 0.08 m. Independently check the saved per-step
trace; `ok`, tool completion and the original sorting score are separate facts.

The current code checks the final hold using sixty one-step waits. This avoids
model reasoning between steps but adds RPC/render work. A faster implementation
needs its own registration and independent validation of the same continuous
criteria.

## Evidence limits and iteration

Initial exploration recovered from failed motions before achieving the declared
hold. It supports the relative recipe, not unattended success of the complete
controller. An untested preparation posture or camera projection must not be
promoted from that result alone.

An independent formal attempt grasped and initially lifted the bottle by about
0.102 m, then failed the fixed-trunk 0.25 m retraction. Its elbow reached its
lower joint limit, EEF position error remained about 0.128 m, and the final
bottle height and upright criteria failed although retention remained present.
Do not treat a successful `grasp` as completion of this entire skill. The
successful rehearsal stopped the empty base much nearer the target than this
failed attempt. Adapting the station or retraction is a hypothesis for a new
code revision, not an already tested recovery or a universal distance rule.

A second formal attempt failed before contact: the fixed right-arm pregrasp
remained about 0.442 m from its target, with several joints near upper limits.
Both hands stayed empty. The current code always selects the right arm and does
not establish that its approach is reachable from every station. Arm selection,
base orientation and shorter checked approaches need fresh exploration; they
are not fixes already validated by these records.

The frozen full controller completed one independent rehearsal and failed both
subsequent formal instances. The initial successful exploration is separate and
does not increase that unattended success count.

Keep one current skill directory; our local Git commits/tags retain prior code.
Preserve failed runs in the evidence index. Two independent unattended successes
of identical code and runner are required for verified status. Known unresolved
code failures keep this entry draft even if other runs succeed. Any behavior or
runner change starts a new draft. Raw videos, observations and simulator data
remain outside Git on NAS.
