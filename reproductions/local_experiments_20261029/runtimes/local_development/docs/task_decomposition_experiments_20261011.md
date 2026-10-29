# Task-decomposition skill: initial no-demo trials

These are autonomous RGB/MCP model trials using `gpt-6-astra` at `low` effort,
public instance 301, seed 0, `MAS_DEMO_MOTION=0`, and a 1500-second controller
budget. The model chose all actions and pixels. The executor could use private
geometry; these are not physical-control or official challenge submissions.
The reference [100-task result page](http://10.76.5.241:8765/navigation100_20261010/behavior100.html)
helped select tasks but is not a matched control cohort.

The [published visual review](http://10.130.140.115:8769/task_decomposition_no_demo_index.html)
uses the repository's `scripts/publish_demo_trials.py` and the same index
template as `demo_motion_first10_index.html`. It links to both five-view
reviews, continuous videos, and audit JSON files. The publisher reads the
two valid trials from `../experiments/task_decomposition_no_demo_20261011`;
the aggregate directory contains symlinks to the original trial outputs.

| Task | Output | Skill use and observed decomposition | Official result |
| --- | --- | --- | --- |
| `can_meat` | [Review and video](../../experiments/task_decomposition_can_meat_20261011_r2/run/review_5view.html) | Read `task-decomposition` before the first `act`; tracked two jars separately, filled and closed the first at 2/2, and reached 1/2 in the second. | Timed out without `finish`; Q=0.4444, 25 actions. |
| `putting_away_Halloween_decorations` | [Review and video](../../experiments/task_decomposition_halloween_20261011_r1/run/review_5view.html) | Read the skill before the first `act`; tracked pumpkins 2/2 and candles 2/3 in one chosen cabinet, placed the cauldron next to a table, then found the last candle under the table. It noted that moving the table might disturb the cauldron relation. | Timed out without `finish`; Q=0.8571, 38 actions. |

Both runs recorded the skill snapshot, action trace, evaluator result, and
five-view video in their output directories. An earlier `can_meat`
attempt (`r1`) stopped before model startup because the launcher received the
BEHAVIOR-1K root instead of its `OmniGibson` subdirectory; it is not a model
trial. The two valid trials show that the model used the skill for counts,
shared destinations, dependencies, and pending final conditions. They do not
establish a task-success improvement: both runs hit the controller time limit,
and there is no same-source, same-budget no-skill control. The observed search
and action latency also limits what can be attributed to task decomposition.

The final skill clarifies that a temporary door closure is provisional and
that moving furniture can invalidate an already completed spatial relation.
The trial snapshots retain the skill text used during each run.
