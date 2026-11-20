# Agentic Mobile Manipulation

面向 OmniGibson / BEHAVIOR 的可审计移动操作研究框架。这个目录整合了学长工作流中采用的视觉选点、导航和工具交互，以及后续开发的 Oracle 几何、模型 Pose、几何程序、cuRobo 执行、原生携物导航、经验库和轨迹回放。

统一流程：**观察 → 模型选择动作与目标 → 执行器规划和执行 → 返回新观察 → 独立验收与归档**。

项目源码、脚本、测试、前端、当前几何程序和经验种子均在本目录。使用说明集中在本 README；`SKILL.md` 和其 references 是模型运行时读取的资源。仿真器、受许可数据集、模型权重、凭据、原始实验录像与运行目录单独安装和保存。

## 目录

- [功能与研究模式](#capabilities)
- [安装](#installation)
- [初始化经验与程序库](#libraries)
- [启动 RGB 和 Oracle 试验](#quickstart)
- [Pose、程序和坐标约定](#manipulation)
- [代码探索与版本管理](#exploration)
- [日志、回放与训练数据](#artifacts)
- [批次评测与客户端](#evaluation)
- [可选在线导航](#navigation)
- [验证范围](#validation)
- [源码结构](#layout)
- [来源与许可](#provenance)

<a id="capabilities"></a>
## 功能与研究模式

| 模式 | 模型看到什么 | 模型决定什么 | 执行方式 |
| --- | --- | --- | --- |
| RGB 基线 | 当前四向 RGB、图像标识、revision 和执行反馈 | 语义动作与当前图像的目标像素 | 私有 GT 几何、导航和选定执行器 |
| Oracle Manip | Oracle 几何、本体状态、四向和双腕 RGB | EEF Pose、夹爪、时长，或生成这些目标的几何程序 | cuRobo 规划、原生控制器和仿真接触 |
| 代码探索 | 当前任务与受限工作区、按需几何和图像 | 编写、复用、修改和冻结 controller / recipe | 隔离开发、独立 rehearsal / formal |
| 历史兼容 | 原记录对应的观测和工具合同 | waypoint、recipe、compiled skill 或符号动作 | 保留对应历史实现 |

RGB 指模型输入边界；RGB 执行器内部仍使用私有真值几何。Oracle 是显式的特权教师模式。第三人称相机用于录像；Oracle 默认有四向与双腕六路模型可见 RGB。`--no-wrist-cameras` 可用于四视角比较。

主要新增能力包括：

- 同一 `manip` 接口支持直接 Pose 和模型编写的几何程序。
- 简单抬升、收臂可以直接给目标；复杂操作优先读取和复用已有程序，必要时再修改或编写。
- 程序返回连续阶段或有序候选，宿主逐段规划、完整开环执行并验收；失败回滚本次 Manip 起点，保留失败证据。
- 持物参与完整碰撞几何，原生底盘关节支持携物运输。
- 保存躯干与双臂的 18 关节恢复姿势，保持两只夹爪指令。
- 文字经验、可执行技能和几何程序分别保存版本、作者、源码哈希和验证状态。
- 回放保留真实调用、内部 Pose、录像阶段、规划路径、执行结果和回滚；录像完整性独立审计。

<a id="installation"></a>
## 安装

### CPU 核心与开发检查

需要 Linux、Python 3.11+。核心契约和 CPU 检查无需安装仿真器。

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[mcp,navigation,dev]'

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q \
  tests/test_model_manip.py \
  tests/test_recovery_posture.py \
  tests/test_recording_coverage.py
```

模型几何程序和探索 controller 使用 `bubblewrap` 与 `prlimit`。Debian / Ubuntu 可安装 `bubblewrap`、`util-linux` 和 `git`；录像需要可用的 FFmpeg。宿主需允许所需的 Linux 用户与网络命名空间。嵌套沙箱可能以 `NETLINK_ROUTE ... Operation not permitted` 拒绝隔离进程，应检查 stderr 后在合适的宿主环境验证。不会退回未隔离的模型代码执行。

### 原生仿真环境

已有实验记录使用以下版本：

| 依赖 | 记录版本 |
| --- | --- |
| OmniGibson / BEHAVIOR-1K | 3.9.2，BEHAVIOR commit `b1979916ec1549b10a4e65e630bc6504a9af1b00` |
| Isaac Sim | 5.1.0 |
| torch | 2.7.0+cu128 |
| BDDL | 3.7.0 |
| cuRobo | StanfordVL commit `78612f45cef52c3fa0298de243a54cd7ca614414` |
| MCP Python SDK | 1.28.1 |

按各项目的官方安装流程准备环境与合法取得的数据集、R1Pro 资产。上表是已记录组合，不代表其它版本已经验证兼容。仿真环境中同样以 editable 方式安装本项目。GPU 驱动、CUDA 和渲染依赖由该环境配置。

使用外部模型控制器时，另行安装并完成 Codex CLI 的个人认证，准备其 Code Mode 运行时。模型凭据保留在用户配置中。代码包没有服务器 API 地址或共享密钥。

### 运行数据与环境变量

从项目根目录执行，先把下面两条路径替换为自己的目录：

```bash
export MAS_RUNTIME_ROOT=/absolute/path/to/your/robot-runtime
export OMNIGIBSON_DATA_PATH=/absolute/path/to/your/omnigibson-data
source runtime/env.sh
```

环境脚本按当前项目位置设置 `PYTHONPATH`，把可变数据放到源码之外。未指定 `MAS_RUNTIME_ROOT` 时，默认使用 `$XDG_DATA_HOME/manipulation-agentic-system`，或 `$HOME/.local/share/manipulation-agentic-system`。该脚本不自动激活 Conda、不自动接受仿真器许可，也不强制使用实验室代理。

| 变量 | 用途 |
| --- | --- |
| `MAS_RUNTIME_ROOT` | 运行结果、可写经验和程序库根目录 |
| `OMNIGIBSON_DATA_PATH` | 自行安装的仿真资产 |
| `OMNIGIBSON_APPDATA_PATH` | 仿真应用数据，默认在 runtime 下 |
| `MAS_EXPERIENCE_ROOT` | 任务/动作文字经验，环境脚本设为 runtime 下的 `experience_skills/` |
| `MAS_PROGRAM_LIBRARY_ROOT` | 独立 Git 程序库，环境脚本设为 runtime 下的 `skill_library/` |
| `MAS_EXPLORATION_SOCKET_ROOT` | 短路径 Unix socket 目录，默认在 `/tmp/mas-exploration-<uid>` |
| `MAS_PYTHON` | `runtime/robot.sh` 使用的解释器，默认 `python` |
| `MAS_USE_PROXY=1` | 显式使用自己安装的 `proxy` 命令，默认关闭 |

<a id="libraries"></a>
## 初始化经验与程序库

仓库的 `resources/library_seed/` 收录当前可复用内容：**18 个几何程序、3 个 compiled skill、4 个任务与5个动作的文字经验**。原作者、源码、当前版本、draft 状态及历史证据索引一并保留。`archive://v02/` 表示历史归档引用，不是本机必需路径或公开下载地址。

首次使用，导入到一个尚未包含经验/程序库的 runtime 目录：

```bash
python scripts/bootstrap_libraries.py --root "$MAS_RUNTIME_ROOT"
python scripts/manage_experience_skills.py list
python scripts/manage_program_skills.py list
```

导入先校验种子哈希，再在 runtime 内初始化新的独立 Git 程序库，核对几何源码和文字经验。已有目录会拒绝覆盖；导入不是新物理验证，也不会启动模型或仿真。

核心程序为 `bottle_grasps`、`place_in_open_bin` 和 `recover_body`。其余程序包含历史开发方案与失败后的改进；模型应先读取源码和说明，依据当前几何选择，不能把所有 draft 当作通用成功技能。文字经验覆盖开收音机、单罐放桶、微波炉开门，以及瓶罐纸分类中的抓瓶/运输/放置工程子目标。

新回合冻结当前任务和同协议经验；Oracle 与 RGB 经验隔离。共享库更新不会改变活动回合的冻结代码。几何程序完成格式检查即可作为 draft 收录、读取和复用；verified 需要独立证据，收录与认证分开。

<a id="quickstart"></a>
## 启动 RGB 和 Oracle 试验

### 先查看命令

激活正确的 Python / OmniGibson 环境并设置上述环境变量后：

```bash
bash runtime/robot.sh sim --task turning_on_radio --gpu 0 --dry-run
bash runtime/robot.sh agent --dry-run
```

`--dry-run` 只显示命令。它不验证 GPU、资产、模型权限或实际图像送达。

### RGB 闭环

在 `runtime/robot_config.json` 设置任务、实例、端口、模型和推理强度。终端 A：

```bash
bash runtime/robot.sh sim --control-mode rgb --task turning_on_radio --gpu 0
```

等待 `MAS_BRIDGE_READY` 后，终端 B 使用同一环境、项目和 `MAS_RUNTIME_ROOT`：

```bash
bash runtime/robot.sh agent
```

两端共用任务、端口和结果目录。多名使用者需分别分配 runtime 目录、GPU 和端口，并保持每个仿真只有一个控制者。当前配置默认 `gpt-6-astra / high`；更换模型与推理强度作为新实验条件记录。

RGB 最小工具集为 `initialize / look / act / finish`；skills profile 增加技能读取与相应任务笔记工具。`initialize` 返回准备好的当前快照，不重置场景。`act` / `look` 返回新的 revision 和 RGB，下一动作使用当前图像。`look` 是实际转动底盘，四向相机采集不需要旋转。需要时间推进时显式 wait。

### Oracle + 几何程序 + 原生携物导航

两个终端都设置：

```bash
export MAS_MANIP_ENGINE=model_program_v1
export MAS_MANIP_TIMING=model_authored_v1
export MAS_NATIVE_TRANSPORT=native_joint_v1
export MAS_NAV_SMOOTH=1
```

终端 A：

```bash
bash runtime/robot.sh sim --control-mode oracle-waypoints \
  --task sorting_bottles_cans_and_paper --gpu 0 \
  --instruction "抓起一个台面瓶子，抬升收臂后运送到敞口垃圾桶，放入并释放，再等待检查。"
```

准备就绪后终端 B 执行 `bash runtime/robot.sh agent`。有内置公开指令的任务可省略 `--instruction`；没有内置指令时必须显式提供，启动器不会编造完整目标。上例为单瓶工程子目标，完整任务仍按原官方评估器计分。也可使用下列显式双终端入口。

终端 A：

```bash
python -m manipulation_agent.vision_cli \
  --backend omnigibson --policy serve --agent-profile oracle_waypoints \
  --goal-skill none --task sorting_bottles_cans_and_paper --instance 309 --seed 0 \
  --ik-backend omnigibson --grasping-mode assisted --wrist-cameras \
  --record-video --unbounded-episode --oracle-transport-policy native_only \
  --port 29541 --output "$MAS_RUNTIME_ROOT/runs/bottle-001/env"
```

终端 B，在就绪后运行：

```bash
python scripts/run_codex_controller.py \
  --isolate-client-storage --agent-profile oracle_waypoints --goal-skill none \
  --model gpt-6-astra --reasoning-effort high --timeout 0 \
  --blocking-tool-yield-ms 2147483647 --auto-compact-token-limit 48000 \
  --rate-limit-resumes 0 \
  --instruction "把一个台面瓶子抓起，抬升收臂后运送到敞口垃圾桶，放入并释放，等待检查。记录单瓶工程结果与完整分类任务评分。" \
  --mcp-command "$(command -v python)" \
  --mcp-args-json '["-m","manipulation_agent.mcp_server","--bridge","http://127.0.0.1:29541"]' \
  --output "$MAS_RUNTIME_ROOT/runs/bottle-001/controller"
```

使用新的结果目录和空闲端口。标准入口的 `finish` 会结束回合；保留仿真供持续开发需要独立管理生命周期与控制权。没有 episode 截止的实验仍可保留初始化和进程清理 watchdog，它们的状态与任务成绩分别记录。

<a id="manipulation"></a>
## Pose、程序和坐标约定

### 模型负责目标，执行器负责规划

简单动作直接调用 `manip(mode="pose", ...)`。复杂操作使用：

```text
list_manip_programs → read_manip_program → manip(mode="program", program, source_sha256, parameters, revision)
```

缺少适用程序时，`write_manip_program` 保存模型代码。程序接口为：

```python
def generate(task, oracle, parameters):
    # 根据当前几何计算，不直接调用机器人。
    return {"phases": [
        {
            "name": "approach",
            "arm": "right",
            "frame": "robot_base",
            "goal": {
                "position_m": [0.4, -0.2, 0.8],
                "quaternion_xyzw": [0.0, 0.0, 0.0, 1.0],
            },
            "gripper": "hold",
            "contact_target": None,
            "control_steps": 90,
        }
    ]}
```

数值仅示意，不是可以直接在任意场景执行的目标。程序也能返回带分数的有序候选，每个候选包含自己的阶段。模型代码在无网络、GPU、机器人 socket 和宿主任务文件的隔离进程中运行，使用冻结输入、标准库和已安装 NumPy。

- `robot_base` 是本次调用起点的移动底盘坐标系；导航后刷新几何，旧基座坐标不能继续使用。
- EEF 目标指 EEF-link 原点；位置单位米，四元数顺序 xyzw。EEF 原点、夹口中心和表面接触点不同。
- `get_oracle_geometry` 按需查询 link、关节和包围盒；`get_manip_geometry` 返回当前 revision 的碰撞网格。
- `gripper=hold` 保持指令；open/close 在目标到位后运行原生夹爪控制，close 指定实际接触对象/link。
- 预抓、接近闭爪、抬升等阶段分别保留目标、规划与结果。阶段之间使用实测状态继续规划，模型没有内部逐控制步回调。
- 候选首段规划未通过且尚未执行时才换下一候选；已经执行的候选后段失败则回滚整次 Manip 并返回模型。
- `recover_body` 输出躯干和双臂完整 18 关节目标，底盘停在当前站位、两夹爪保持，不是恢复历史仿真快照。

### 执行政策

| `MAS_MANIP_TIMING` | 含义 |
| --- | --- |
| `model_authored_v1` | 当前模型 Pose/程序默认；模型自主给出时长，不按关节速率补时；躯干和双臂 18 关节的位置限位及相关裁剪关闭，保留原生电机动力学和碰撞 |
| `model_estimated_v1` | 按模型配置的关节速度、加速度和 jerk 校验，需要时延长完整路径；不是固定 100 步上限 |
| `explicit_fast_v1` | 历史显式限速、每段最多 100 步的比较策略 |
| `historical_full_trajectory` | 保留历史完整轨迹的比较策略 |

时长与限位政策是实验条件。`model_authored_v1` 不等同于真实机器人机械约束；其它模式需按其记录的版本和实际运行合同使用。碰撞检查、路径连续性、终态到位和持有验收独立保留，失败证据不会因回滚被删除。

### 单瓶入桶流程

<!-- bottle-flow:start -->
1. 从当前观察选择可达瓶子和敞口桶，空手导航到合适站位。
2. 刷新瓶体网格、手指偏移和夹爪开度，读取并复用适合的抓取程序；预抓和接近闭爪逐段规划、执行和验收。
3. 保持原生抓持，抬升并收臂；持物移动前尽量让躯干接近世界竖直，复核整机和物体外形。
4. `navigate_to` 进行原生携物运输，保持手臂与躯干关节目标；起点占用重叠时先原生调整，不把 invalid_start 当成倾角门槛。
5. 到桶附近重新读取瓶/桶网格和实测手瓶变换，选择 `place_in_open_bin`。
6. 程序按真实桶口截面、整瓶通过半径和瓶底越沿高度计算 EEF；到位后仅在显式 `release=true` 时开爪。
7. 自然等待至少 90 步后检查 Inside/released，再等待 60 步复核。两次端点检查与连续逐步稳定证据分别记录。
8. 单瓶工程结果与完整分类任务官方评分分开。

当前默认采用 `model_authored_v1` 时由模型自主提交每段正整数 `control_steps`；显式选择其它策略时记录请求和实际时长。完整路径、夹爪顺序、原生 assisted 抓持、碰撞检查和失败回滚均保留。程序和流程包的 draft 代表验证程度，不阻止读取和复用；跨实例认证另行完成。
<!-- bottle-flow:end -->

<a id="exploration"></a>
## 代码探索与版本管理

`scripts/run_exploration_pipeline.py` 提供 `prepare / status / run / evaluate`，由操作方提供环境、任务、GPU、端口、源码快照和独立评估配置。`prepare` / `status` 本身不调用模型。完整配置结构以脚本和 `exploration_factory.py` 为准。

```bash
python scripts/run_exploration_pipeline.py prepare \
  --session /absolute/path/to/new-session --config /absolute/path/to/session.json
python scripts/run_exploration_pipeline.py status --session /absolute/path/to/new-session
```

开发先在同一存活环境中调通；`register` 冻结代码和运行器，`rehearse` 使用独立环境，`submit / formal` 使用精确冻结版本。模型通过受限文件和机器人接口工作，没有原始宿主 shell。普通模型程序模式与历史 recipe / compiled skill 协议分别保留，不能混用参数。

长期会话使用 `take_notes` 保存版本化摘要，在确认的子任务边界通过 `checkpoint_subtask` 交接上下文。完整聊天、失败和代码留盘；笔记与模型声明不代替独立评估。实际动作使用串行控制、request_id 去重和 revision 校验。恢复时先核对在途调用，避免重复动作。

`manage_experience_skills.py` 管理文字经验，`manage_program_skills.py` 管理 compiled skill，`publish_geometry_assets.py` 管理固定几何程序/流程包。后者从本 README 的单瓶流程标记区提取说明。共享程序库使用自己的 runtime Git 历史，工作区有未提交人工修改时拒绝覆盖；项目源码目录与运行库分离。

<a id="artifacts"></a>
## 日志、回放与训练数据

常见产物包括：

| 文件/目录 | 内容 |
| --- | --- |
| `run.json`、`events.jsonl` | 配置、源码来源、真实工具调用、观察与独立评估 |
| `model_events.jsonl` | 模型客户端原始输出，保留原始行和缺失情况 |
| `canonical_events.jsonl` | 跨客户端的派生事件，标注来源和原行号 |
| `model_pose.jsonl` | 原始/规范化 Pose、阶段与请求关联 |
| `model_pose_trajectory_*.npz` | 规划和执行轨迹 |
| `open_loop_trials/` | 阶段计时、录像、终态和回滚记录 |
| `recording_segments.json` | 分段录像索引，历史前缀和续录分别保留 |
| `skill_snapshot`、程序快照 | 该回合实际读取的经验和源码版本 |

具体名称以实际版本的运行记录为准。回放在原 Manip 参数位置显示内部实际 Pose，不伪造额外模型调用。视频可播放不等于内容和尾段完整，`recording_coverage.py` 会比较录制索引、解码帧和最终仿真步。

`report_server.py`、`replay.py`、`replay_assets/` 与 `web/` 提供门户、时间线和回放；`scripts/publish_oracle_replays.py` 等脚本生成派生展示。前端原有系统说明页属于历史展示资源，当前使用说明以本 README 为准。运行服务器与端口由使用者自行分配。

用于 waypoint 学生训练时，输入使用动作前 RGB、可部署本体状态和任务历史；Oracle 网格、物体真值和抓持状态放在教师/审计元数据。保留目标 EEF、阶段、夹爪和完整执行路径。事后补写的说明不冒充原始推理，内部阶段没有新增观察时不虚构中间模型输入。

已有离线 Point→Pose 试验使用存档深度反投影；当前在线 Agent 工具尚未开放独立 `Point/locate`。核心执行器保留像素反投影能力，不能将离线示例描述为已上线的工具。

<a id="evaluation"></a>
## 批次评测与客户端

保留 `prepare_behavior100.py`、`behavior100_remote.py`、批次监督器、资源租约、失败恢复及官方评估接入。任务、实例、种子、模型/强度、实际相机、控制协议、源码/资产和主机/GPU 来源随每轮记录。批次启动前准备自己的资源配置；不沿用历史实验室主机配置。

独立官方评估器判定任务成绩。以下检查分别报告：

1. 完整任务或指定工程子目标是否成功。
2. 模型是否调用 finish、客户端如何退出以及清理是否完成。
3. 模型调用与仿真记录是否按名称、参数和顺序对齐。
4. 录像是否真实更新、覆盖末尾并与轨迹同步。
5. 程序属于开发演示、同实例复跑还是独立跨实例认证。

客户端适配层保留 Codex、OpenCode、Kimi，以及原生 Responses 循环。Codex 有历史真实模型/仿真记录；其它客户端的 parser 和配置能力不能直接算作真实 RGB 送达验收。`--probe` 只检查可执行文件和所需参数，不检查认证或任务成功。

测试夹具中 `codex_recorded.jsonl` 是历史真实 finish 事件摘录；`opencode_format.jsonl`、`kimi_format.jsonl` 使用合成数据和假图像，只验证格式。它们均不是基准分数。

<a id="navigation"></a>
## 可选在线导航

默认路线使用私有 GT 地面支持和当前碰撞几何，不需要 Cartographer。`native/cartographer/` 保留深度在线占用图工作进程，使用 Cartographer 2.0 的原生概率栅格和观测射线；位姿来自特权仿真定位，不是完整 SLAM，也不把预制地图当作在线观测。

可选组件需独立准备 Cartographer、Eigen、absl、protobuf、Lua 等 C++ 构建依赖：

```bash
cmake -S native/cartographer -B /absolute/path/to/cartographer-build \
  -DCMAKE_PREFIX_PATH="$CONDA_PREFIX" \
  -DMAS_CARTOGRAPHER_EIGEN_INCLUDE_DIR=/absolute/path/to/matching/eigen3
cmake --build /absolute/path/to/cartographer-build
```

Eigen 头文件必须匹配静态库构建；历史有效组合使用 Eigen 3.4.0。`package_runtime.py` 导出独立 ELF 依赖闭包与来源清单，配置 `MAS_CARTOGRAPHER_RUNTIME` 指向导出前缀。在线组件失败时不回退成 GT 地图而伪称在线成功。

`native/cartographer/upstream/` 保存学长原版集成的 C++ 快照、补丁和许可。其原始 README 按原字节保留为 `UPSTREAM_NOTES.txt`，供来源审查；其中 Habitat 专用步骤不属于本项目默认启动流程。

<a id="validation"></a>
## 验证范围

已有原生记录支持：

- instance 309 的一次 Astra/high 单瓶抓取、收臂、原生携物导航与放桶，释放后等待 90+60 步两次 Inside/released 为真；使用 assisted 抓持和取消 18 关节位置限位的条件，完整分类任务未最终评分。
- 一次右手持瓶的 18 关节恢复和 60 步保持；该结果不证明左手、双手或跨实例通用性。
- 收音机、单罐放桶和开门等历史运行有各自范围的证据，详情以经验记录的 claim 和来源为准。

部分历史录像存在冻结或缺少尾段的问题，原生工程结果与录像质量分开；发布源码不会修复或补造这些帧。当前几何程序为 draft，不能将库中全部程序宣称为已认证技能。

本次 GitHub 目录整理不重新运行模型或仿真。校验范围记录在根 `SOURCE_MANIFEST.json`；静态检查、CPU 测试、模型实测和原生任务验收是不同等级。

<a id="layout"></a>
## 源码结构

```text
README.md                         统一使用说明
NOTICE                            第三方归属和许可文本
pyproject.toml                    Python 安装入口
src/manipulation_agent/           Harness、协议、客户端、执行器、经验库、回放
scripts/                          单轮、探索、批次、经验维护和展示脚本
runtime/                          可配置环境、sim/agent 启动器与示例配置
skills/                           模型运行时技能和 references
program_skills/                   参数化动作、恢复姿势与探索提案源码
resources/library_seed/           当前几何程序、compiled skill 和文字经验种子
task_designs/                     动作约定和长程任务结构
tests/                            CPU 测试、适配器检查和格式夹具
native/cartographer/              可选 C++ 在线占用图组件及上游快照
third_party/                     第三方许可
web/                              原有门户和历史系统展示前端
SOURCE_MANIFEST.json               发布目录的文件哈希与整理范围
```

开发时可运行 `python -m unittest discover -s tests -v` 检查 unittest 用例；pytest 用例使用 `python -m pytest tests`。部分测试需要 torch、FFmpeg、CLI、编译器或允许命名空间的宿主，应按实际依赖和测试条件运行。CPU/fake 通过不代替真实仿真。

`scripts/run_s134.sh` 保留原命令入口名称，发布版已改为使用当前项目位置和用户环境；不再依赖原服务器目录。源码目录没有携带原开发 `.git`、缓存、环境、日志或模型认证。

<a id="provenance"></a>
## 来源与许可

本项目是独立组织的 OmniGibson Harness。保留并标注使用的学长代码、适配算法和参考工作，不将全部功能宣称为本项目原创，也不需要安装学长完整 Habitat 工程才能使用默认流程。

| 来源 | 固定参考 | 在本项目中的用途 |
| --- | --- | --- |
| [dadwadw233/habitat-gs](https://github.com/dadwadw233/habitat-gs)，`jinkai/harness` | `0815cf234ee591bacd8017e9b1def4fac13e649b` | 视觉选点 GT 导航采样/排序、工具交互和客户端模式参考 |
| 同仓库 `jiarui/memory-slam` | `136950b2fb419b90d2607d4cfc6832f3957eeaa5` | `native/cartographer/upstream/` 原始集成快照与许可 |
| [llz3724/agentic_mobile_manipulation](https://github.com/llz3724/agentic_mobile_manipulation) | `619605e362cecf0e54eedd4dbe34636671d5cf15` | 仿真所属线程、评估生命周期与适配方式参考 |
| [Zhouwenb0/iGibson_Agentic_System](https://github.com/Zhouwenb0/iGibson_Agentic_System) | `6c1edd605619ee30db43533baa9295f67e714bb2` | 显式要求、有状态记忆与反馈流程参考 |
| [RAX](https://github.com/robot-agent-exploration/rax) | `db744f5950582c7c301f249192c0ed3c76517dd8` | 技能组织、探索和独立验证生命周期参考；未打包其机器人环境或资产 |
| [BEHAVIOR-1K](https://github.com/StanfordVL/BEHAVIOR-1K) | v3.9.2 | 仿真器、官方任务/评估、资产及原生状态依赖 |

第三方许可原文保留于 [NOTICE](NOTICE)、`third_party/` 和 `native/cartographer/` 中的 LICENSE 文件；仍按各自许可使用。项目顶层没有替作者新增整体开源许可，第三方许可不自动覆盖本项目全部原创代码。数据集、Isaac Sim、机器人资产和模型权重按各自条款单独获取。
