# Hierarchical goal planning and the matched ablation

`goal-decomposition` teaches a revisable goal tree with variable depth and branching.
The agent writes the root as a concrete terminal state in its own words. Requirements
and constraints from the instruction remain a separate coverage ledger. Any goal can
have children and further descendants; different branches need not have the same
depth or number of children. `parent_id` expresses decomposition; `depends_on`
expresses prerequisites, including across branches. Both relations are acyclic.
The current goal's immediate steps and next check live separately in `active`.
Completed children do not prove their parent without checking its result. New
observations can expand or revise the tree and reopen disturbed results without
erasing requirements. The skill is generic; it supplies no task-specific BDDL or
evaluator feedback to the model.

The shared `task_notes` MCP tool stores up to 12000 characters of model-authored text.
A write replaces the current note using `expected_version`; stale writes fail and
same-request retries are idempotent. `task_notes.json` stores the current snapshot;
accepted writes and all rejected attempts remain in the ordinary append-only event
ledger. Reads and writes do not advance physics or consume act/look steps. The
store accepts free-form text and does not prescribe a hierarchy or assess its truth.
A model after context compaction can read the same notes again.

The hierarchical skill specifies `goal-tree-v2` JSON in
`skills/goal-decomposition/references/working-state.md`. The tool itself does not
validate that tree: schema compliance and model claims are visible evidence, not
an automated planner or evaluator. Compact snapshot replacement is deliberate for
the current 100 tasks; richer goal-edit operations can be tested separately later.

For the matched ablation, `--goal-skill hierarchical` exposes the skill
and instructs the model to read it before acting. `--goal-skill none` omits its
catalog entry, resources and instruction. Both arms use the same seven tools,
other frozen skills, common prompt, working-note storage, task instructions,
instances, seeds, source commit, model, execution budget and independent evaluator.
The shared visual skill no longer references a missing goal skill. Both arms must
write an initial intended approach (`--require-task-notes`); the control chooses its
own format and decomposition. An early act/look without notes is rejected and
counts as an attempted step in both arms. There are no scripted targets or plans.

The historical paired experiment has 100 attempts per arm with gpt-6.1-sol low, 50 act/look attempts including
failures, no episode wall deadline and no policy retries. Startup and closure
watchdogs stay separate. The experiment estimates the effect of the hierarchical
skill over an otherwise equally equipped GPT controller; it does not isolate
planning from persistence by withholding storage from the control.

Subsequent long-horizon development runs use unbounded episodes under the user's
October 22 instruction. Frozen attempts keep their original skill and budget; the
v2 revision does not rewrite historical v1 notes or change an active episode.

Replay exports preserve the note version before and after each tool call and the
actual agent-step number. Replay displays recognized v2 trees at their authored
depth, with statuses, dependencies, evidence, active steps and revision notes.
Legacy v1 L1/L2 records remain supported. Invalid parent graphs and arbitrary control
prose stay verbatim; no missing hierarchy or hidden reasoning is invented.
Earlier action steps never inherit a later completed tree. The official score,
model completion claims, transport alignment and process exit remain separate.

Report overall and instruction-source-stratified success (first 50 detailed and
last 50 short phrases), paired discordant outcomes, Q score, act/look attempts,
model tokens and infrastructure failures. Planning adherence (skill read, initial
notes/tree, revisions) is descriptive, not a substitute for goal quality or task
success. Preserve failed attempts and missing scores. One seed does not establish
repeatability or the long-horizon research claim.
