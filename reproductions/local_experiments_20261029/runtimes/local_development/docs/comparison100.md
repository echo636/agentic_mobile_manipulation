# Three execution modes on 100 matched tasks

Fresh experiment: `operations/compare100_20261002` (outside Git). Each arm contains the same 100 task instructions, public instance 301, seed 0. The task and BDDL hashes are checked against the preserved original manifest and assets on both execution hosts. No task-specific hints are added. Fifty instructions are from the official gallery and fifty retain the project's previous static BDDL descriptions; this is recorded rather than presented as 100 official natural-language instructions.

| Arm | Branch | Profile | Execution |
| --- | --- | --- | --- |
| Original | `main` | `skills` | Project GT navigation, controlled carry and checked placement |
| Motor | `explore/astra-motor` | `motor` | Astra programs over physical velocity, joint and gripper controllers |
| Official | `explore/official-symbolic` | `official` | Direct upstream symbolic primitives, including state and pose changes |

All arms use GPT-6 Astra, four simultaneous RGB views, 80 executor commands, 240 tool calls, 20,000 environment steps and 1,800 seconds of model/controller time. Startup has a separate 1,200-second budget; simulator units have a 4,200-second hard deadline. A motor command and an object-level semantic action differ in granularity. These are three complete execution conditions, not an equal-physical-work action-count ablation or an official leaderboard submission. Failures, partial progress and unscored crashes remain in the fixed 100-task denominator. The comparison also reports the subset where all three arms have final scores.

Five disjoint GPU lanes are provisioned on S115 and S134. Every launch checks GPU UUID, VRAM, user-cgroup memory, disk, quota, encoder and assets. S115 has two 14-GiB process reservations within a 32-GiB user cgroup; S134 has three 14-GiB reservations. The shared-GPU admission floor permits 14 GiB free, matching the prior real S134 component and policy validation. The selected S115 lanes require 24 GiB free. Availability is rechecked before every episode; running unrelated GPU jobs are never stopped. Host and GPU differences mean elapsed times are not a controlled hardware comparison.

The controller's optional `--isolate-client-storage` uses a process-local bubblewrap bind for native session files, keeping HOME, CODEX_HOME and existing credentials unchanged. `sqlite_home` uses task-specific local storage; `log_dir` uses the private episode directory. Provider-returned summaries are exported from that episode's native session directory. This avoids exhausting the controller host's root quota without moving active or unrelated sessions. The existing read-only model sandbox and disabled non-MCP tools remain in force. [Official Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).

Original JSONL output, generated motor programs, tool arguments/results, RGB, actual simulation steps and independent scores are retained. Each arm's asynchronous replay renderer produces the real-time and readable video editions with the existing synchronized transcript UI. Render status is separate from task score. The comparison dashboard presents one row per task and three execution results; raw/private model files remain outside the web root.

[Live comparison](http://10.76.5.241:8765/compare100_20261002/).
