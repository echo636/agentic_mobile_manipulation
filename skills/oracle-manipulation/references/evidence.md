# Evidence provenance

Historical V02 evidence is referenced by `archive://v02/runtime/`; its large
outputs and original environments are not bundled. For new launches, activate
the user's simulator environment and source `runtime/env.sh` from the repository.
Explicitly select `--ik-backend omnigibson` because the legacy CLI default is
custom DLS. Preserve the model and effort recorded in each historical run;
new-run defaults are configured in `runtime/robot_config.json`.

## Radio

Run: `runs/oracle_official_ik_20261029_081435_71e43b`.
Validated in `operations/oracle_official_ik_20261029/validation.json` and raw
`env/events.jsonl`, `env/waypoint_trace.jsonl`, `controller/model_events.jsonl`.
Four surround RGB plus Oracle 3D; official IK; assisted mode. Official task success
true, 5 press attempts, 2346 official-IK control steps. No wrist-input benefit claim.

## Bottle

Run: `runs/oracle_bottle_wrist_upright_20261029_113405_30f048`.
Validated in its `engineering_validation.json` and
`operations/oracle_bottle_wrist_upright_20261029/validation.json`.
Six RGB plus Oracle 3D, official IK and native assisted retention. First grasp failed,
second succeeded. Final world lift 0.27827276 m, torso tilt 0.012229 degrees and
height over base 1.142283 m; final 60-step combined criterion passed. No held-object
navigation. Engineering success true; full recycling task official success false.
The scene preparation restored 11 absent fixed floors from the same full template;
task bindings and original assets were unchanged. This is one successful episode,
not proof of friction grasping, complete sorting, or a wrist-camera success-rate gain.

The two historical models above preceded this skill and did not read it.

## New transfer trials

Both used Astra/xhigh, six RGB views plus Oracle 3D, official IK, native assisted
retention, instance 301 / seed 0 and an unbounded episode. Both formally finished.
Both actually read SKILL.md and references/grasp.md through MCP from frozen bundle
`9b1760f7a076e86f5fbcea58fb9fc0f7fc3046434f1aa43e3cc92fbb3cf5faaf`.
The placement/articulation references and updated entrypoint were written afterwards;
only future episodes can test this updated version.

Can run: `runs/oracle_transfer_can_placement_20261029_201056_dca3d1`.
Its `engineering_validation.json` and `transfer_validation.json` verify one native
pick/place into the task bucket, release, retreat and 60 consecutive position-stable
steps (3045–3104), zero position drift, and no held-object navigation. Actual grasp
lift was about 0.112 m. Official six-can goal satisfaction was 1/6; full task false.
The same-template scene preparation restored one absent fixed floor, preserving task
bindings and original assets.

Door run: `runs/oracle_transfer_microwave_door_20261029_194648_19c826`.
Controller source is recorded in controller_selection.json, with three earlier
client startup failures preserved; those failures had zero model invocations.
Native assisted door retention and six rotation segments were verified. Final door
angle was 0.6527785063 rad after release/retreat and a 60-control-step wait. The
before/after wait samples agree; per-step door joint logging is absent, so strict
continuous hold is unverified. Original full popcorn task remains false. This scene
restored 11 same-template floors, preserving task bindings and original assets.

The common independent checker and journals are under
`operations/oracle_transfer_20261029`. Viewer: the repository's existing
`web/replays.html` and shared replay player, published at
`archive://v02/replays/transfer_20261029/replays.html`.
All startup failures are retained in a separate portal source. Codex cleanup after
a received finish is separately recorded; exit -15 from that cleanup is not an
absent finish or a changed official score. No no-skill control or repeated-seed
study was run; these episodes do not establish a causal success-rate improvement.
