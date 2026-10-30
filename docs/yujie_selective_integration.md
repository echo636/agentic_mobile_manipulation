# Selective integration of PR #6

Source: yujie PR #6, `bbe9b5f230cae81c58bc945ccb15aaaf4c01a206`,
`reproductions/local_experiments_20261029/runtimes/local_development/`.
The previously imported hot-drink and groceries task packages are retained.
Historical experiment archives and runtime copies are not dependencies.

| Extracted change | Current location | Validation |
| --- | --- | --- |
| Workflow costs and plan replacement audit | `reporting/workflow_efficiency.py`, `reporting/goal_replay.py` | R511 pizza and R520 study/kitchen counters match their runtime records; rejected note writes do not replace accepted plans |
| Default saturation system names | `executors/compat/material_conditions.py` | R530 actual mop: native `water.n.06` lookup fails; normalized condition is false when dry and true after water saturation |
| Alternative cleaning solvents | `executors/compat/solvent_conditions.py` | R530 actual mop: dry, water-only and detergent-only cases match native washer rules; capacity and nonmatching conditions remain enforced |
| Visibility after staging and door movement | `skills/pick-and-place/SKILL.md` | Wording adapted to the existing target-locked approach; autonomous benefit remains unmeasured |

The two material adapters operate at native condition construction. They do not
change motion, collisions, particle simulation or task evaluation. The solvent
adapter changes conjunction to disjunction only for saturation-only clauses
whose set exactly matches the native knowledge-base washer alternatives. This
is a readiness semantics fix, not a repair for every cleaning failure.

Run the offline report against preserved evidence:

```sh
python scripts/analyze_workflow_efficiency.py /path/to/run \
  --controller-dir /path/to/controller --output /path/to/new-report.json
```

The report distinguishes admitted requests, executor actions, finish claims and
independent evaluator results. Plan-node disappearance, opposite door actions
and failure-following intervals are review candidates. Intervals may overlap;
they are not an additive recovery-cost measure or proof of poor planning.

Explicit-navigation requirements, additional decision/confirmation fields,
payload pinning, collision disabling and whole-scene restoration wrappers are
excluded. Existing image-history pruning, videos, goal-tree replay and cloth
import support already cover corresponding archived implementations. Deleted
online mapping code remains excluded. Fixed-body NextTo proposals, cloth
topology freezing, contact warmup and scene-shell filters remain candidates for
matching reproduced faults; they are not installed speculatively. Current
catalog loading already clears room filters.

Validation records and origin hashes are stored under
`operations/yujie_pr6_review_20261030/selective_integration_20261031/` and
`operations/long_horizon_full_v2_20261022/mas_material_conditions_r530/`.
R529's earlier probe queried a stale native saturation cache before advancing
the environment; R530 corrects that diagnostic timing with unchanged candidate
code. These are component contrasts, not autonomous task successes.
