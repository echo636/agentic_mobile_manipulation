# 实验运行与可视化记录指南

这份指南适用于本仓库的通用机器人实验。重点是启动实验、确认正常运行、保存完整输出，并生成可复查的 HTML 和视频记录。每条实验还必须发布一个可从内网打开的可视化网址；仅保留本地文件路径不算完成。

## 0. 强制实验配置

启动、解释或比较实验配置前，查阅[实验配置清单](experiment_configuration_inventory.md)：当前有六种 agent profile；profile、skill 处理、执行/评分、预算、结束监督与线上 schema 必须分别核对。`--long-horizon-planning-skill on` 同时改变工具和提示协议，不能仅解释为增加一份 skill 文档。

除非实验负责人明确指定其他配置，统一使用：

- 模型：`gpt-6.1-sol`
- 推理强度：`low`
- 无 demo motion 实验：使用 `scripts/run_no_demo_trial.py`，记录为 `demo_motion=false`
- demo motion 实验：使用 `scripts/run_demo_trial.py`，记录为 `demo_motion=true`

启动命令中必须显式写出：

```text
--model gpt-6.1-sol --reasoning-effort low
```

不要只依赖脚本默认值。每条实验的记录必须包含模型、推理强度和 `demo_motion` 状态；对照实验只能改变一个研究变量。

## 1. 固定路径

工作目录：`/mnt/data/home/sunyujie/robot_manipulation/agentic_mobile_manipulation_7f388e4`

Python：`/mnt/data/home/sunyujie/miniforge3/envs/behavior/bin/python`

常用目录：

- OmniGibson：`../behavior_challenge_sunyujie/BEHAVIOR-1K/OmniGibson`
- 实验输出：`../experiments/<run-name>`
- 可视化站点源目录：`review_sites/atomic_actions_20261008`
- 内网静态站点根目录：`/mnt/data/home/sunyujie/robot_manipulation/review_sites/atomic_actions_20261008`
- 内网访问地址前缀：`http://10.130.140.115:8769/`

## 2. 启动实验

无 demo motion 实验使用 `scripts/run_no_demo_trial.py`：

```bash
cd /mnt/data/home/sunyujie/robot_manipulation/agentic_mobile_manipulation_7f388e4
OMNIGIBSON_DEBUG=1 /mnt/data/home/sunyujie/miniforge3/envs/behavior/bin/python \
  scripts/run_no_demo_trial.py \
  --task <task-name> \
  --instruction '<task instruction>' \
  --output ../experiments/<run-name> \
  --omnigibson-source ../behavior_challenge_sunyujie/BEHAVIOR-1K/OmniGibson \
  --provider-profile <provider-profile> \
  --codex-home ../experiments/demo_motion_codex_home \
  --expected-provider-host <provider-host> \
  --gpu 0 --appdata ../experiments/<appdata-name> \
  --timeout 3600 --max-actions 80 --max-sim-steps 20000 \
  --model <model-name> --reasoning-effort <low|medium|high|xhigh>
```

文件输入时把 `--instruction` 换成 `--bddl-source <path-to-task-file>`。有 demo motion 的实验使用 `scripts/run_demo_trial.py`，其余参数相同。每次运行必须使用新的 `--output` 目录。

历史上的热饮语义魔法对照使用 `--semantic-magic`，模型通过 `act_semantic` 写物品名称，由执行器自动接近目标。它只评估按名称选择动作和顺序，**不满足用户要求的 RGB 选点与显式导航协议，不得作为正式自主选点实验**。此协议的 `observation_mode` 为 `rgb_semantic_magic`，应与 `rgb_only` 实验分开记录。

在上述历史对照中，“模型自主”仅指模型自己选择原子动作、物品名称和先后顺序；命名实例定位、路径规划、接近目标、抓取和摆放效果由执行器完成。`semantic` 配置只公开 `initialize`、`act_semantic`、`finish`，没有模型显式调用的 `navigate_to`。审阅 HTML 从执行器记录展示每段内部导航，但这些路径不能解释成模型自主导航决策。

## 3. 预算和运行监控

`--max-actions` 是主要动作预算，`--max-sim-steps` 是仿真安全上限，`--timeout` 是墙钟 watchdog。运行较慢时先检查日志，不要因没有即时输出就终止。

结束监督须单独核对：热饮高层协议会用`premature_finish`拒绝尚有预算时的`blocked`，当前原生杂货协议会接受正式`blocked`；两者的动作数不能直接解释成模型持续性差异。2026-10-25热饮120动作回合在第24动作已第一次请求blocked、共6次被拒绝。检查首次finish、全部拒绝及其后动作，而不只检查最后接受的finish；无正式finish的文本结束另查控制器续接。证据与造任务要求见[案例GROC-021](bugfix_journal.md#groc-021不同结束监督导致其他实验不会提前停的错误比较2026-10-25)。

工具缺参时先核对实际HTTP请求中的schema和`strict`。GROC-022发现Codex导出的机器人工具为`strict:false`，即使目录声明`required`也会生成不完整调用；真实失败前缀重放比只有一句提示的探针更能复现。`scripts/trace_responses_transport.py`默认仍原样转发；显式加`--strict-mcp-tools`时改为`strict_mcp_tools_v1`，只将机器人函数设为严格模式、补齐schema的必填字段声明，保留输入历史/图像和响应字节。启动该进程后，用`MAS_RESPONSES_PROXY_URL=http://127.0.0.1:<port>/v1`连接本轮runner，并冻结代理源码与启动参数。该模式是协议变更，不能称为透明观察器；日志同时记录原始/转发请求哈希与schema。第三方端点的严格模式不能直接套用官方服务保证，仍检查实际返回参数、本地校验及独立评分；见[案例GROC-022](bugfix_journal.md#groc-022工具目录有必填字段但实际请求关闭严格模式2026-10-25)。

热饮语义魔法正式回合按用户要求固定使用 `--reasoning-effort low`。曾误设为 `high` 的回合只保留为诊断，不计为正式结果。此前 7200 秒墙钟上限内只执行了 55/80 次动作，之后被 watchdog 中断；正式回合应使用至少 `--timeout 14400`，并保留 80 次动作预算。墙钟时长包括模型推理、连续行走、物理步进和逐帧视频编码，不等于机器人在仿真中的动作时长。该回合记录显示动作接口约占 6593/7202 秒，模型调用间隙约 577 秒；缩短模型推理不会消除逐步仿真的主要耗时。记录每次回合的 BDDL SHA：修订后的热饮定义接受茶匙放在茶几上的餐巾等合理叠放方式、点心放在盘子或托盘等摆法，并取消未告知机器人的空水瓶最终位置要求；不同 SHA 的分数不可直接比较。

```bash
ps -eo pid,etime,cmd | rg '<run-name>|run_no_demo_trial|run_demo_trial|vision_cli|run_codex_controller'
tail -20 ../experiments/<run-name>/controller/model_events.jsonl
tail -20 ../experiments/<run-name>/controller/canonical_events.jsonl
tail -50 ../experiments/<run-name>/simulator.log
```

只有完整路径匹配当前 run 才能操作进程；不要使用宽泛的 `pkill -f isaac`。出现 `turn.failed`、`501`、`429` 或 `segmentation fault` 时保留日志并记录故障。

## 4. 结束检查

```bash
cat ../experiments/<run-name>/summary.json
```

记录 `controller_exit_code`、`formal_finish_observed`、`official_task_success`、`video_status`、模型、推理档位和三个预算字段。控制器正常退出不等于任务成功。原生 BEHAVIOR 回合以 `official_task_success` 为准；高层热饮回合该字段为 `null`，只能报告 `evaluation.task_success`、11 项语义目标和协议审计，不能称为官方挑战成功。

## 5. 生成可视化审查

```bash
PYTHONPATH=src /mnt/data/home/sunyujie/miniforge3/envs/behavior/bin/python \
  scripts/audit_demo_trial.py ../experiments/<run-name>
ls -lh ../experiments/<run-name>/run/review_5view.html
ls -lh ../experiments/<run-name>/run/episode_browser.mp4
```

审查 HTML 和视频时确认初始画面、动作前后 RGB、目标物体和机器人是否可辨认、最后状态是否可判断，以及视频是否完整可播放。

视频时长必须来自真实记录的仿真帧。正式实验和正式可视化运行必须显式设置 `OG_SKIP_PHYSICS_STEPS=0`。设置为 `1` 时只会保留动作边界截图，视频会按 30 FPS 快速播放且不代表真实运动过程；这种诊断视频必须标注为 diagnostic，不能作为正式连续视频交付。

正式运行还必须显式设置 `OG_KINEMATIC_NAV_TELEPORT=0` 和 `MAS_NAV_RELAXED=0`。前者关闭端点瞬移，导航会沿网格路径逐控制步执行并记录 `base_motion.jsonl`；后者禁止绕过可见性约束的宽松回退。GPU PhysX 场景可使用 `OG_DISABLE_CONTACT_CACHE=1`。

以下两段描述**旧版物理水粒子实验**，不适用于下方的 `--high-level-harness` 视觉长程实验：热饮 runner 会在初始化瓶中水之后切到 CPU 刚体物理，随后恢复接触记录，使模拟器原生 `Touching`/`OnTop` 谓词可用于该物理实验评分；`contact_cache_mode.json` 留存切换证据。以后创建 `cooked__water` 系统时，程序临时满足 OmniGibson 的 GPU 初始化检查；这个兼容处理不改变给机器人的指令或 BDDL 目标。**物理水粒子实验不得设置 `OG_CLEAR_WATER_PARTICLES`**：该诊断开关会删除初始水粒子，使 `(filled water_bottle water)` 失真并阻断烧水目标。

热饮的 `pour` 是理想化的魔法原子动作：大容器向小容器倒水后，按官方 `Contains` 检查目标中有水，同时保留源大容器的 `Filled` 状态，表示只倒出一部分。杯子不必达到官方 `Filled` 的 20% 体积阈值；BDDL 目标是 `(contains mug cooked__water)`。向更大的烧水容器倒入普通水时，还要检查目标的官方 `Filled` 阈值。CPU 粒子模拟可能让已装满的水壶在搬运后只剩少量熟水；原子动作在倒水及放置后恢复并验证原先应保持的装满状态。真实模拟器短诊断必须先制造不足阈值的水壶，再验证水壶和杯子两个关系同时为真，不能只依赖动作接口返回成功。

### 高层规划实验模式

如果实验目标是评估机器人能否自行规划长程依赖，而不是验证液体粒子、接触和支撑物理，使用 **`--high-level-harness`，不要使用 `--semantic-magic`**，并把 `--bddl-source` 指向 `tasks/prepare_and_serve_hot_drink/high_level_visual.bddl`。模型只获得四路 RGB，通过原有 `act` 接口选像素和原子动作，行走必须自行选择 `primitive=navigate_to`；抓取、放置、倒水和烧水等操作不会暗中导航。执行器仅把模型所选像素对应到当前可见物体，再以高层关系实现魔法效果。录像仍按连续物理步记录。视觉版 BDDL 去掉初始水粒子，runner 为每次运行建立独立 BDDL overlay，不改动安装版定义。

该模式的 `evaluation.task_success` 是 **high-level harness** 判定，`protocol=high_level_hot_drink_state_v1`，`official_task_success=null`，不得与 OmniGibson 原生 BDDL 分数混写或声称官方挑战成功。关闭的冰箱/橱柜不能取物、倒水或向其中物品摆放，持物时不能开门或操作炉灶；搬运托盘时所有直接和嵌套的高层子物品都随托盘移动。餐具、点心和托盘沿支撑关系最终到达茶几即可。保持 `--reasoning-effort low --max-actions 80`，`MAS_DEMO_MOTION=0`、`OG_SKIP_PHYSICS_STEPS=0`、`OG_KINEMATIC_NAV_TELEPORT=0`；高层模式把步行速度设为 1.2 m/s，仍沿模型选点的导航路径逐控制步运动。

热饮视觉回合给机器人的任务文本必须原样使用：

> 请准备一份热饮和点心：把冰箱里的水烧开，橱柜里有水壶、杯子、餐巾、餐垫、盘子、茶匙、托盘、糖饼干和蜂蜜；将饮品、餐具和点心布置在客厅茶几上。

不要在任务文本或系统提示中给动作先后顺序。**自 2026-10-18 起，`task-decomposition` 已并入 `long-horizon-planning`，旧 skill 和 `--task-decomposition-skill` 开关不再用于新回合。**两组都必须通过最新 RGB 的 `image_ref` 和归一化 `point` 选目标；`navigate_to` 也必须由模型显式调用，禁止 `act_semantic`、按名称指定物体或执行器暗中替模型导航。比较两组时保持场景、模型、预算、指令和其余参数一致，分别记录完整审计。

**统一长程规划 skill（后续新回合适用）**：用 `--long-horizon-planning-skill on|off` 控制；默认 `off`。开启后 `agent_profile=planning`，普通视觉操作 skill 仍在目录中。模型先读 `long-horizon-planning/SKILL.md`，把最终结果拆成有前提依赖的子目标图，只规划当前可执行子目标的局部动作；BDDL 任务再读 `long-horizon-planning/references/bddl-goals.md`，处理量词、任选分支和配对。每次工具结果后更新状态，失败时局部重规划。**这不是给热饮任务预写动作顺序。**该 profile 另提供 `update_plan`、`remember`、`recall`，且每次 `initialize/look/act/finish/list_skills/read_skill/update_plan/remember/recall` 调用均须带公开 `decision={state,active_subgoal,reason,expected}` 四项简短说明；缺少任何一项会在执行前被拒绝，动作不会消耗。公开决策不是模型未公开的内部思维链。审计原始 `events.jsonl`、`replay.json` 和审阅 HTML 的“本步公开决策”，同时检查显式 `navigate_to` 与 RGB 选点；审阅视频也展示公开决策。开启组与关闭组若要比较，须固定其他变量；旧回合没有这份 skill，不能合并统计。

固定实例的橱柜初始为**打开**，冰箱初始为关闭；新运行前核对画面、实例文件和高层初态一致。导航规划使用静态通行网格及动态物体边界，沿路径逐步移动机器人；它没有证明可活动柜门绝不会因接触改变状态。高层逻辑中的门状态只随显式 `open`/`close` 更新，因此若物理门被碰开，RGB 与高层状态可能失配。审查录像时检查导航前后的柜门和冰箱门；如出现非指令导致的门状态变化，应记录为场景/状态一致性故障，不应把该回合当成可靠的规划结果。

**新版烧水接口（2026-10-17 起）**：模型在当前 RGB 中选中水壶或灶台像素，调用 `act(primitive="boil", target={"image_ref": "<最新图像>", "point": [x, y]}, revision=<当前版本>)`。仅当水壶已在灶台且含普通水、机器人手为空时成功；返回 `effect.water_boiled=true`，代表此次高层烧水动作完成，模型无须从画面猜测水是否沸腾。`wait` 不再累积热饮任务的烧水进度，不能用等待代替 `boil`。此确认是**动作结果**，不是整项任务的成功信号；仍须布置杯子、餐具和点心。旧版通过 `wait` 加热的录像与日志保持不变，并由审计按旧规则重放；不同接口版本的实验分开统计。v13 正式回合使用仅灶台目标的首版 `boil`，42 次动作后 0/11 且 10 次 `boil` 均未成功；录像显示目标视觉定位困难。新版接受模型选中的水壶或灶台，开/关门动作也必须验证物理门状态，不能只修改高层状态。两项修正尚待新整回合验证。

新回合建议沿用下列显式参数（其余 provider、GPU 和路径参数按第 2 节填写），并使用新的输出目录：

```text
MAS_DEMO_MOTION=0 OG_SKIP_PHYSICS_STEPS=0 OG_KINEMATIC_NAV_TELEPORT=0 MAS_NAV_RELAXED=0
scripts/run_no_demo_trial.py --high-level-harness --bddl-source tasks/prepare_and_serve_hot_drink/high_level_visual.bddl
--instruction '请准备一份热饮和点心：把冰箱里的水烧开，橱柜里有水壶、杯子、餐巾、餐垫、盘子、茶匙、托盘、糖饼干和蜂蜜；将饮品、餐具和点心布置在客厅茶几上。'
--model gpt-6.1-sol --reasoning-effort low --max-actions 80 --timeout 14400
--long-horizon-planning-skill on  # 对照组改为 off；两组分别使用新输出目录
```

2026-10-16 的首次完整高层回合 `formal_hot_drink_high_level_low_v5_20261016` 有一处评分入口覆盖缺陷：机器人完成的 31 个动作按高层状态满足 11/11 项目标，但原始 `run.json` 误用了 OG 物理评分并记为失败。该原始记录应保留。`scripts/audit_demo_trial.py` 从成功动作日志独立重放状态机，逐条与 `high_level_state.jsonl` 核对，并另存 `posthoc_semantic_audit.json`；网页同时显示重算结果与原始 OG 失败。后续回合已修复重复的 `RGBBackend.evaluate()` 定义，使正式评分直接使用高层协议。这个追溯审查仍不是 OG 官方任务通过。

旧版只加载厨房和客厅，窗外花园未加载，机器人在窗边会看见缺地面的天空和悬空门框。高层协议额外加载同一场景模板中的花园作可视上下文；修复只适用于之后新录制的回合，旧录像保持原样。不要加入走廊或餐厅：这两个区域的墙体与现有厨房/客厅导航图可行路径相交，机器人会走进墙体。

首次重录 `formal_hot_drink_high_level_low_v6_20261017` 在发现旧系统提示写有抓取、倒水和加热的任务相关依赖后主动中止，不作为独立规划结果。后续 `semantic` 系统提示仅说明工具调用、反馈与结束协议；不再提供此任务的动作依赖。检查模型实际收到的提示应以该回合 `controller/controller.json` 中记录的 `developer_instructions` 为准。

`formal_hot_drink_high_level_low_v7_20261017` 在无任务依赖提示下达到高层状态 11/11，但加入走廊/餐厅后机器人掉到地板下并进入墙体；录像不能作为合格的视觉证据。审查脚本现在检查机器人底盘相对初始高度的最大偏移，v7 因约 1.48 米偏移标记为视觉失败。高层执行器为后续回合在物理步后稳定底盘高度；还需检查墙体/导航图的一致性，不能仅凭高度指标认定录像合格。

`formal_hot_drink_high_level_low_v8_20261017` 在用户指出按名称接口违反原子动作选点要求后中止；仅保留原始证据和 `PROTOCOL_ABORTED.txt`，不计入正式成绩。后续视觉回合必须验证 `agent_profile` 与 skill 开关一致、没有 `act_semantic`、每次目标操作携带当前 RGB 的 `image_ref` 和 `point`，每段导航对应模型显式发出的 `act(navigate_to)`。

`formal_hot_drink_visual_pixel_low_v9_20261017` 首次验证了上述 RGB 协议和显式导航，但执行器只接受一个固定台面实例，拒绝模型选中的其他可见厨房工作台；保留为中止的诊断。之后的视觉执行器在放置时接受同类厨房工作台，并把物品放到**模型实际选中的实例**，不替换选点。`formal_hot_drink_visual_pixel_low_v10_20261017` 与 `formal_hot_drink_visual_pixel_low_v11_20261017` 是保留的完整模型失败回合：两者均使用 `low`、80 次预算、无 task-decomposition skill、原文指令、RGB 选点和显式 `navigate_to`。v10 完成烧水关系 2/11 后提前报告受阻；v11 在水壶位于普通台面时开炉等待，最终高层目标 0/11，约 40 次动作后报告受阻。两轮导航日志与模型调用对齐，连续视频和底盘高度检查通过；**均不得称为任务成功**。审阅页分别是内网 `/hot_drink_real_assets_20261015/10_rgb_pixel_explicit_nav_partial/review_5view.html` 和 `/hot_drink_real_assets_20261015/11_rgb_pixel_explicit_nav_retest/review_5view.html`。

2026-10-17 新版烧水实验已完成并发布在 `http://10.130.140.115:8769/hot_drink_boil_review_20261017_index.html`。v12 在发现 `boil` 成功反馈被公共边界过滤后主动中止，仅留诊断日志；随后修复 `effect.water_boiled=true` 的公开反馈。v13 共 42 次动作，10 次 `boil` 均未成功，0/11。v14 修复开/关门的物理状态核验，并允许对模型选中的水壶或灶台调用 `boil`；63/80 次动作，冰箱内水瓶被抓取、水成功倒入水壶、烧开并返回确认、餐垫放上茶几，最终 **3/11**，模型却报告 `achieved`，独立评分仍判失败。v15 模型实际读取了 `task-decomposition/SKILL.md`，5 次动作后报告受阻，**0/11**。**v13/v14 使用了 `minimal` 配置，连原有操作 skill 也没有提供；v15 使用 `skills` 配置。因此这些回合不是仅改变 task-decomposition 的有效对照，不能据此判断该 skill 的作用。**三轮均非官方 BEHAVIOR 成绩，原始 HTML/视频仍保留供故障分析。2026-10-17 修正后，热饮视觉回合始终使用 `skills` 配置，开关只改变 task-decomposition 是否出现在 skill 目录；后续比较必须以修正后的两组新回合为准。

修正后的 skill 关闭组是 `formal_hot_drink_visual_boil_skill_off_low_v16_20261017`：`agent_profile=skills`，`skill_manifest.json` 保留 `failure-recovery`、`pick-and-place`、`visual-exploration`、`visual-manipulation`，仅排除 `task-decomposition`。控制器日志显示模型实际读取了前三种视觉/抓取 skill。它在 49/80 次动作后报告受阻，独立评分 **2/11**（烧水两项），没有完成杯子和茶几布置。v15 的 skill 开启组也为 `agent_profile=skills`，清单仅多 `task-decomposition`，模型确实读取，5 次动作后受阻，**0/11**。两组均使用相同模型、低推理强度、原文指令、场景实例和动作预算；这是修正后可供审查的配置对照，但各只有一次样本，不应据此推断 skill 的稳定效果。内网 index 中 v15/v16 的完整 HTML 和视频可并排检查，v13/v14 仅用于追查先前接口与配置错误。

**提前结束修复（后续新回合生效）**：v15 的第 5 次动作后出现两次可恢复的视觉选点失败，模型仍提交 `finish(blocked)`，接口错误地接受并关闭回合。热饮高层回合现在在 HTTP 结束请求和模拟器结束执行两处拒绝这种 `blocked`：只要动作、调用和仿真步预算尚未耗尽，且没有不可恢复的 `navigation_invalid_start` 或 `physics_instability`，就返回 `premature_finish`，保留当前回合与最新 RGB，要求模型继续尝试。第一次实跑 v17 验证了 `finish(blocked)` 被拒绝后仍有后续动作，但模型改报 `finish(aborted)` 仍可关闭回合；因此进一步将 `aborted` 限于监督器，并让过早的 `achieved` 接受一次私有完成校验，不提供各项目标细节。这个二元校验会告诉模型其结束声明尚未成立，属于新的实验协议，和旧版“结束即关闭、评分完全不反馈”回合应分开统计。审阅时核对 `tool_result.error.code=premature_finish` 或 `supervisor_only` 之后是否继续有 `act`，并分别报告模型结束声明与 `evaluation.task_success`。这个修复不追溯修改 v15/v16/v17 的原始记录。

修复后的正式验证回合是 `formal_hot_drink_visual_finish_guard_low_v18_20261017`：原文指令、`gpt-6.1-sol/low`、`skills` 配置且 task-decomposition 关闭、RGB 像素选点、模型显式 `navigate_to`、`MAS_DEMO_MOTION=0`、`OG_SKIP_PHYSICS_STEPS=0`。模型在第 43 和第 68 次动作后两次提交 `finish(blocked)`，接口均返回 `premature_finish`，其后分别继续执行动作；最终 80/80 次动作耗尽后接受 `finish(blocked)`。独立高层目标 **6/11，任务失败**，已完成烧水、水壶热水、餐垫、盘子、杯中热水和杯子上桌，未完成餐巾、茶匙、托盘、饼干和蜂蜜。后验状态重放与原始高层状态日志一致，模型/模拟器工具调用一致，导航均来自模型显式 `navigate_to`；视频为 3817 帧、约 127.23 秒，审计通过。内网审阅首页 `http://10.130.140.115:8769/hot_drink_finish_guard_20261017_index.html`，其中 v17 为只拦截 `blocked` 的诊断，v18 为完整结束约束的正式验证。两轮均非官方 BEHAVIOR 通过。

**120 次预算诊断（2026-10-17）**：保持同一任务、实例、指令、`gpt-6.1-sol/low`、RGB 像素选点、显式导航、普通操作 skill 和 task-decomposition 关闭，仅把 `--max-actions` 改为 `120`；GPU 改用当时有空余显存的 GPU 0。v19 在 29/120 次动作、目标 0/11 后，模型反复提交被拒绝的 `finish(blocked)` 并自行退出；默认一次新线程续跑后仍未正式结束，监督器以 `aborted` 关闭。为排除续跑次数过低，启动脚本增加 `--unfinished-restarts`（默认仍为 1）；v20 使用 6 次上限，但在 32/120 次动作、目标 0/11 后，模型连续 83 次调用不存在的 `x/read_mcp_resource`，没有新的有效机器人动作，因此手动结束该异常循环。两轮视频均完成，后验状态重放一致，但控制器审计失败；**它们不是有效的 80 对 120 预算比较，不能推出 120 次足够或不足**。内网审阅首页 `http://10.130.140.115:8769/hot_drink_120_budget_20261017_index.html`。不要把本轮停止原因写成动作预算耗尽或任务成功。

本节旧回合段落中的 `task-decomposition` 名称和旧开关只描述当时的配置与证据；2026-10-18 之后的新回合按上方统一长程规划 skill 的启动参数执行。

**统一规划 skill 首轮实验（2026-10-18）**：`formal_hot_drink_planning_skill_low_120_20261018_r1` 使用原文指令、`gpt-6.1-sol/low`、`--long-horizon-planning-skill on`、120 次动作预算、RGB 选点、模型显式 `navigate_to`、`MAS_DEMO_MOTION=0` 和 `OG_SKIP_PHYSICS_STEPS=0`。模型读取新 skill 后建立了 5 节点依赖图，完成取水、烧水并布置大部分物品；独立高层状态重放为 **8/11，整项任务失败**，缺糖饼干、杯子上桌和杯中热水。模型在用尽 120 次动作后如实报告未完成。全部模型/模拟器调用对齐，每次公开决策字段齐全，导航路径均来自显式选点，底盘高度稳定，8386 帧连续视频约 279.5 秒；这些协议与可视证据检查通过，不代表目标通过或 BEHAVIOR 官方成绩。运行墙钟约 4227 秒。DAG 只有 2 次成功写入（初建及烧水后），把多数物品合并为一个粗节点，后半程未持续更新；`remember` 零次。120 次动作中有 45 次 `navigate_to`、18 次 `invalid_visual_target`、9 次 `out_of_reach`、7 次 `wait`；模型曾 3 次尝试提前 `finish(blocked)`，受结束约束拒绝后又用部分 `wait` 消耗预算。这说明 skill 有助于前段依赖链和诚实终报，但**尚未解决局部反复试错与计划状态维护**。旧 80 次 v18 为 6/11，预算不同；旧 120 次 v19/v20 审计失败，因此不能把本轮 8/11 当作有控制的 skill 增益。完整内网审阅页：`http://10.130.140.115:8769/hot_drink_planning_skill_20261018_index.html`。

## 6. 发布内网可视化网址（强制交付项）

发布前按[回放 HTML 配置标注要求](replay_configuration_review.md)逐项核对：单场页面顶部展示实际配置及可下载 JSON，比较首页标出组别与协议差异。当前长程两组仅有长程 skill 组开放三个计划/记忆工具；两组均保留 decision。历史未知项明确标注，不能用最新默认值补齐。

每次实验都必须有一个能从实验内网直接打开的 index 页面，并从该页面链接到五视角审查、回放和连续视频。`../experiments/<run-name>/run/review_5view.html` 是本地原始证据，不能替代内网网址。

发布时使用服务器实际服务的目录（不要把页面只写入仓库内的同名目录）：

```bash
mkdir -p ../experiments/<cohort-name>
ln -sfn ../<run-name-1> ../experiments/<cohort-name>/01_<label-1>
ln -sfn ../<run-name-2> ../experiments/<cohort-name>/02_<label-2>
python scripts/publish_demo_trials.py \
  --trials ../experiments/<cohort-name> \
  --site-root /mnt/data/home/sunyujie/robot_manipulation/review_sites/atomic_actions_20261008 \
  --index-name <cohort-name>_index.html \
  --cohort-title '<cohort title>'
INTERNAL_URL=http://10.130.140.115:8769/<cohort-name>_index.html
curl -fsSI --max-time 5 "$INTERNAL_URL"
```

`curl` 必须返回 HTTP 200；失败时先检查 8769 端口和服务器根目录，再记录故障，不能把 `file://` 路径当作交付链接。最终记录中的“内网可视化网址”必须使用经过 `curl` 验证的完整 URL。

单条审查页面位于 `../experiments/<run-name>/run/review_5view.html`；发布后的页面地址为 `http://10.130.140.115:8769/<cohort-name>_index.html`。

## 7. 记录模板

```text
实验名称：
任务：
输出目录：
模型和推理档位：
输入来源：
动作预算 / 仿真步预算 / 墙钟 watchdog：
控制器退出码：
任务是否成功：
视频状态：
单条审查页面：
比较 index 页面：
内网可视化网址（已用 curl 验证）：
失败原因或观察：
```

原始输出、`summary.json`、视频和 HTML 都保留后，才算完成一条可交接记录。


原生任务可显式设置`MAS_REJECT_FALSE_TIME_CLAIMS=1`启用`clock_claim_correction_v1`（GROC-024）：公开预算充足时纠正显式虚假时间/会话耗尽声明，具体技术blocked仍接受。默认关闭；需冻结环境变量和harness源码，并读取run.json.termination_contract。它属于结束监督变化，不能当作模型主动坚持改善或与旧终止协议合并比较。
