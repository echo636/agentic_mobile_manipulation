# Development branches

The execution experiments were separated on 2026-10-02. Branch selection and `--agent-profile` selection are distinct: the default profile remains `skills` on each branch.

| Branch | Responsibility | Experimental profile |
| --- | --- | --- |
| `main` | Original RGB agent, project executor, evaluation and replay | None of the two new profiles |
| `feat/harness-fastmcp` | Main-based shared harness upgrade: FastMCP, policy adapters, tool context, native RGB loop | Original profiles; base `c56c8d6` |
| `explore/astra-motor` | Astra composes bounded Python over physical base, joint and gripper controls | `motor` only |
| `explore/official-symbolic` | RGB target selection directly dispatches the upstream symbolic primitives | `official` only |
| `explore/astra-primitive-composition` | Preserved mixed history through `91e36fd`; existing experiment provenance | Historical `motor` and `official` |
| `fix/executor-v7` | Historical executor repairs; all commits are contained in main | Historical |
| `fix/supported-shelf-20261001` | Local historical selected-shelf support experiment | Historical, not published |

The motor branch starts from the motor-only commit `4455008`. The official branch starts from main `d5a579d` and transplants only the official additions from `c91a054` and the recorded results from `91e36fd`, resolving integration against the main interfaces. It does not include the motor interpreter, motor tools, backend or prompt.

On 2026-10-04 the shared harness changes were brought from `feat/harness-fastmcp` into `explore/official-symbolic`, followed by Official-only frame and budget repairs. Motor exploration remains paused.

Shared observation, scoring, MCP, replay and original profiles remain available in both branches. Future shared fixes can be committed on main and brought into each experiment explicitly. Push only to `echo636/agentic_mobile_manipulation`.

## Validation provenance

The original physical motor radio trial ran frozen `d3611a0` and failed the task (Q = 0). The original official symbolic radio trial ran frozen `c91a054` and achieved the task (Q = 1). Their recordings and source snapshots are immutable. A branch split and CPU regression checks do not constitute new simulator runs or comparative success-rate evidence.

The ongoing original32 replay generation uses frozen `e777672`; branch switching does not modify that worktree. The original32 controller batch ended on 2026-10-02 at 01:45 Asia/Shanghai. These are point-in-time facts, not a live status feed.

Maintenance journal, CPU checks, branch isolation checks and publication verification: `operations/branch_split_20261002/` in the shared project workspace (outside Git).
