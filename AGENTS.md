# Manipulation system

The active checkout on this host is `/mnt/data/home/sunyujie/robot_manipulation/agentic_mobile_manipulation`, on `feat/long-horizon-tasks`. The integration target is `main`; the maintained origin is `echo636/agentic_mobile_manipulation`. The workspace's `archive/` contains historical source and worktrees. Legacy path symlinks serve existing processes and evidence; make new source changes in this checkout.

Read [the code map](docs/code_organization.md) to locate the current module before editing. The package has one implementation of each active module, organized under `agent/`, `runtime/`, `transport/`, `executors/`, `tasks/`, `batch/` and `reporting/`. Keep simulator imports in the executor adapter; core code and tests run on CPU. Follow [local integration notes](docs/local_workspace.md) when using archived task protocols or moving workspace files.

Run checks with `PYTHONPATH=src` and verify the imported package path. This host already has `/mnt/data/home/sunyujie/miniforge3/envs/behavior/bin/python` with pytest, NumPy, SciPy and Torch; the system Python lacks optional test dependencies. Use `PYTHONPATH=src <python> -m pytest -q`. Native BDDL goal tests additionally need the pinned `behavior_challenge_sunyujie/BEHAVIOR-1K/bddl3` on PYTHONPATH. A skipped test is not passed validation.

## Runtime and experiments

The current RGB protocol and motor behavior are documented in [README](README.md) and [local manipulation](docs/local_manipulation.md). `initialize` returns the prepared four-camera snapshot; each `act`/`look` returns the next snapshot. The model selects current-image targets. Private geometry, task bindings and evaluator state stay outside model feedback. Changes to local approach, navigation actuation, feedback, tools, termination or scoring create a new recorded protocol; archived explicit-navigation and high-level hot-drink runs retain their original source and scores.

New model experiments default to `gpt-6.1-sol` / `low`. Record the actual model, effort, task/scene/source/skill hashes, tools, budgets, termination supervision, interpreter, GPU identity and validation scope. Freeze these before starting; preserve every failed attempt. Keep startup and cleanup watchdogs separate from episode budgets. Keep task success from the independent evaluator separate from controller finish and evidence/video integrity. Missing evaluation remains unknown.

Follow [task construction and readiness](docs/custom_long_horizon.md) for new tasks, and [atomic action experiment requirements](docs/atomic_action_experiment_requirements.md) for model runs. Definitions and built-task catalogs live in `task_designs/`; `task_designs/drafts/` is explicitly unbuilt. The imported yujie task README records current versus historical executable scope.

Prefer fixes at the shared capability that failed. Verify the original failure and affected valid paths. Local research bypasses such as suppressing physics, disabling collisions or repeatedly restoring poses remain in their frozen experimental sources; do not silently promote them into the native default. A scripted probe or CPU test establishes only its stated scope, not autonomous task success.

## Evidence and workspace

Keep generated runs, reports, assets, environments and operational records outside Git, under the workspace's `experiments/`, `review_sites/`, `runtimes/` or `archive/` as appropriate. Existing reference repositories and installed assets remain separate from this project. Preserve immutable episodes and bookmarked replay paths.

Historical local research notes are indexed in [docs/archive/local](docs/archive/local/README.md). Use them to trace prior failures, not as the current launch configuration. The historical hot-drink state machine is available only for offline audit in `tasks/hot_drink_state.py` and `reporting/legacy_hot_drink.py`; it does not change current native scoring.

Use bounded actions, structured errors and serialized simulator access. Keep credentials out of source and logs, preserve upstream snapshots, and publish only within the user's authorized scope.
