# Independent manipulation system

This is a new project, not a fork of the source repositories. Keep simulator imports inside the OmniGibson adapter. The core must run on CPU without simulator dependencies.

The maintained origin is https://github.com/echo636/agentic_mobile_manipulation.git, explicitly authorized by the user. Develop this repository; do not push to reference repositories. Keep generated runs, assets, credentials and environments outside Git. Current observation is four fixed RGB cameras (front/back/left/right); no wrist camera or robot rotation is used to collect surround views.

This worktree is dedicated to custom long-horizon task development, explicitly authorized by the user on 2026-10-19. Work in `/mnt/nas/home--xujingyi/agentic_mobile_manip/repos/manipulation-agentic-system-long-horizon` on `feat/long-horizon-tasks`, created from `feat/harness-fastmcp@924fa8c`. The original `repos/manipulation-agentic-system` worktree remains in use by other agents; make this task's source changes here.

Use `feat/harness-fastmcp` as the PR merge target. Do not create a separate integration branch solely to merge a PR. Resolve conflicts on this feature branch and complete relevant validation before merging.

Reuse environments and assets under the shared workspace root; keep new run outputs and operations records in separate long-horizon batches under `/mnt/nas/home--xujingyi/agentic_mobile_manip/operations/`. Historical custom task instances and evidence are in `operations/long_horizon_execution_20261015/`; preserve their frozen inputs and scores. The current task definitions are in `task_designs/runtime/`, with validation requirements in `docs/custom_long_horizon.md`.

New brain-model experiments default to `gpt-6.1-sol` with reasoning effort `low` (user instruction, 2026-10-13). Preserve the model/effort recorded in historical runs. New BEHAVIOR100 batches default to 50 agent act/look attempts and no episode wall-time deadline; initialization and post-budget closure watchdogs are separate from task execution.

2026-10-22 update: the user removed the action-count limit for subsequent tests. New starts, including queued tasks not yet launched, use explicit `unbounded` episode policy: no action-count, episode physics-step, or wall-time cutoff. Keep recording act/look counts. Preserve the original policy of already running and historical attempts; initialization and post-finish process cleanup remain separately monitored.

The default model protocol starts with initialize, which returns the prepared episode's current four-camera snapshot without a reset or extra render. Each act/look response supplies the next snapshot; wait is an action when simulation time must advance. The minimal profile exposes initialize/look/act/finish, and skills adds list_skills/read_skill/task_notes. The latter is an episode-local, versioned model-authored text store shared by both goal-skill ablation arms. Keep observe and start/get/cancel observation jobs confined to legacy workflow compatibility. Default prompts and skills must not require those hidden tools or an extra observation before finish. All client prompts come from vision_policy.system_prompt.

Never label a mock, scripted policy, oracle-state experiment, or symbolic execution run as a visual autonomous policy or an official challenge submission. Record source, assets, task instance, host, interpreter, GPU UUID, PID/unit, configuration and validation level for each run. A tool success or agent finish claim is not task success: use the independent task evaluator.

For new atomic-action model experiments, reviews, and result publication, follow [the experiment requirements](docs/atomic_action_experiment_requirements.md).

Keep operational steps, faults and fixes in journals and structured records; do not create an HTML report for every operation. The existing retest32_v7 replay URL shows only the historical 32 tasks; use a separate summary page for current comparisons. Preserve bookmarked replay links, distinguish cohorts, and publish compact display data rather than full evaluator payloads.

Do not log credentials. Do not modify upstream source snapshots or unrelated environments. Use bounded actions, structured errors, serialized simulator access and immutable episode records. Preserve failure evidence.

- 2026-10-22 用户明确成功口径：只要独立官方任务评估器判定成功就计任务成功；finish、客户端退出、录像和证据完整性单独记录，不能据此否定任务成功。用户授权 241（本机双4090）、134、112、115、118 全部机器的可用计算资源纳入后续调度，不局限于原先几台。
