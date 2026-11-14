# 跨区长程调度基准任务（3 项）与强模型基线实验汇总（2026-11-14）

本文档面向项目合作者，完整汇总合并后代码基线（本地分支 `feat/long-horizon-tasks`）上新建并验收通过的 **3 项跨区域长程调度基准任务**、**Benchmark 与 Harness 两侧代码改动清单**、**强模型（`gpt-6.1-sol`, `low`）基线回合的全套实验配置**、**六项效率指标同表结果**及**逐步行为诊断**。

> **实验产物与录像存放位置（遵循仓库规则，大文件与运行日志存放在 Git 仓库外）**
> - 本机汇总目录：`/mnt/data/home/sunyujie/robot_manipulation/experiments/hot_lunch_benchmark_20261113/`
> - 远端仿真机（`workstation-master-zx`，RTX PRO 6000）镜像目录：`/data/zx/sunyujie/robot_manipulation/experiments/hot_lunch_benchmark_20261113_pro6000/`
> - 六项指标机器可读 JSON：`/mnt/data/home/sunyujie/robot_manipulation/experiments/hot_lunch_benchmark_20261113/three_tasks_model_vs_ref_metrics.json`

---

## 一、代码基线：相对 `origin/feat/long-horizon-tasks` 的本地改动与归属

当前本地仓库 `/mnt/data/home/sunyujie/robot_manipulation/agentic_mobile_manipulation` 的 `feat/long-horizon-tasks` 分支以合并上游 `origin/feat/long-horizon-tasks` 后的提交 `cea45ef` 为底座，截至 `HEAD = a267904` 新增 18 个原子提交（均只在本地提交，未推送远端）。

所有改动严格按以下原则划分：
- **Benchmark 侧（评测基准与执行器，对所有被测方法固定不变）**：场景构建、原生最短路与放置执行器缺陷修复、机器人时钟、后台定时家电过程、独立评估器与统计口径。
- **Harness 侧（智能体运行设施，作为方法对照项）**：全部做成命令行显式开关，**仓库默认一律为 `off`**，不改变原有默认行为。

### 1.1 本地新增提交逐项说明（“改之前 → 改之后 → 为什么”）

| 提交范围 | 归属 | 涉及主要模块 | 改之前 → 改之后 → 为什么 |
|---|---|---|---|
| `98b2873`、`b0bed62`、`43b3042`、`f748831`、`5ed1575`、`55dd0ff` | **Benchmark 侧** | `executors/navigation/`、`executors/placement.py`、`executors/local_stances.py` | **改之前**：① 自建任务新增的固定台面未写入场景自带静态导航图，机器人站位被选进台面内部；② 深餐桌受窗与餐椅夹击时 `1.4 m` 内无可走格直接报错；③ 在 `2 cm` 薄盘上放物时，包围盒碰到下方桌面被误判重叠而把热狗推到盘沿滑落；④ 点中餐椅正下方的地面灰尘时因终点格不可走直接报无法到达。<br>**改之后**：① 导航前将规格声明的固定台面屏蔽出可通行图；② `1.4 m` 内无站位时退回 `<= 2.0 m` 最近可走格并同步放宽当次臂展检查；③ 落点打分排除薄支撑物正下方 `6 cm` 内的承托台面；④ 点击不可走地面时走到 `<= 2.0 m` 最近连通可走格并如实回报 `incomplete`。<br>**为什么**：在厨房整理版（`kitchen_v2_tidy`，200–203 步）参考验收中消除非决策性几何卡死。 |
| `5cb6d02`、`02b1d0b`、`8a57cb4` | **Harness 侧**（默认全 `off`） | `mcp_server.py`、`scripts/run_codex_controller.py`、`agent/vision_policy.py` | **改之前**：`gpt-6.1-sol (low)` 在长任务中常执行 9–12 步零报错动作后自行调用 `finish(aborted)` 提前放弃，或中途停止调工具输出阶段汇报文字。<br>**改之后**：新增三个独立开关（默认均关闭）：① `--finish-guard on`（步数充裕时将首次 `achieved` 或非连续失败下的过早 `aborted` 退回一次，要求结合最新观测核实）；② `--unfinished-turn-nudges N`（未调 `finish` 即结束发言时在同一会话续接提醒）；③ `--autonomy-instruction on`（系统提示词末尾补充无人中途回复、持续调工具直至完工的中性说明，不含任何任务策略）。<br>**为什么**：解决长程自主回合早退问题，且保留开关供消融对照。 |
| `57a0bbe` | **Benchmark 侧** | `runtime/robot_clock.py`、`reporting/efficiency_table.py`、`scripts/report_efficiency_table.py` | **改之前**：合并仓库缺少统一的机器人时间账本与六项指标同表汇总脚本。<br>**改之后**：接入机器人时钟 `robot_clock`（策略 `robot_clock_v2_native_path`：按 `navigation_plans.jsonl` 原生 A* 路线长度 ÷ `0.5 m/s` + `0.10 m` 折线简化转向角 ÷ `60°/s` + 每次实际执行的非导航操作 `10.0 s` + `wait` 实际秒数累计，写盘 `robot_clock.jsonl`）及 `report_efficiency_table.py`。<br>**为什么**：使“路程节省、等待并行、操作增减”折算到同一物理时间标尺下可比。 |
| `2a3e3c4`、`2ac2c72` | **Benchmark 侧** | `executors/timed_processes.py`、`tasks/custom_tasks.py`、`executors/custom_task.py`、`tools/action.py` | **改之前**：自建任务缺乏随机器人移动与操作在后台读秒的通用定时家电机制，且 `wait` 单次上限过短、参考流程不允许不同策略含不同阶段集合。<br>**改之后**：实现 `timed_processes`（`timed_processes_robot_clock_v1`）：家电满足“关门 + 开启”期间按 `robot_clock` 增量自动累计有效加工时长，完成后写入目标谓词并在公开回执 `effect.process` 中返回运行中或完成提示音状态（支持超时烤焦失败配置）；`wait_seconds` 单次上限设为 `120 s`；`FullWorkflowReference` 支持 `allow_strategy_variants: true`。<br>**为什么**：支撑微波炉、洗衣机、烘干机、烘干窑炉等真实“启动后可转身干别的事”的并行调度结构。 |
| `6ee76ea` | **Benchmark 侧** | `executors/carry.py`、`executors/placement.py`、`executors/omnigibson_rgb.py` | **改之前**：抓起装有多件餐具/物资的敞口载具（托盘、盘子、藤编衣篮）做符号化跨房间移动时，内部承托物未随载具位姿同步而散落；冰箱/橱柜门关闭后偶发铰链微弹越过极小开门阈值。<br>**改之后**：对已确认支撑在敞口载具上的刚体载荷在手持搬运期间保持相对位姿同步（放下载具后恢复独立物理沉降与评估），并在 `close` 成功后清零关节速度锁止至下次 `open`。<br>**为什么**：保证“用托盘/衣篮一次端多件”这一真实提效动作物理可靠，同时保留载具面积容量与摆放碰撞约束。 |
| `682df4b`、`a94f4ed`、`a267904` | **Benchmark 侧** | `executors/closure_clearance.py`、`executors/inside_sampling.py`、`executors/placement.py`、`reporting/efficiency_table.py` | **改之前**：① 向滚筒洗衣机放第 2 件衣物时单次释放落点靠近舱门前口，关门时被内嵌门塞挤出；② 向开放高衣柜放衣物时 `select_inside_release` 将边缘悬挑域 `support_full_volume` 与 90° 侧立姿态 `unladen_face_proposal` 同平放内缩域混排，导致袜子竖在柜口翻出；③ 双抽屉柜 `fillable` 标注底面比碰撞底板高 1–3 mm，零容差过滤掉了真实抽屉底板支撑点。<br>**改之后**：① 按关门状态下门板干涉量排序（排除共移抽屉盒体）；② 零碰撞同分时严格优先搁板内缩域 `support_inset` 与平放静息姿态；③ 对确认带支撑的内部落点统一允许 `<= 15 mm` 齐平底板偏差，并补充原生 Codex 会话 token 文件回退读取。<br>**为什么**：消除单次几何释放执行器在洗衣机、衣柜、抽屉上的物理假失败。 |

---

## 二、三项跨区长程调度基准任务的规格与设计

三项任务均遵循统一标准：**① 无感知陷阱**（无重名同款干扰物、无 `fv2_*` 合成几何块、窄小工具等比放大至 `> 6–7 cm` 可稳定点击尺度）；**② 公开指令与评估器 100% 策略中立**（只声明初始位置与终态/安全先后要求，绝不规定操作顺序或强制要求使用载具）；**③ 双特权参考流程实机验证**（“最优并行+载具分批”与“习惯顺序干等+单件往返”均在 PRO 6000 上 0 失败跑通，且最优策略节省 **32.7%–42.1% 机器人时间**与 **40.0%–62.3% 路程**）。

| 维度 | 任务 1：双人热午餐 L2（`hot_lunch_living_l2`） | 任务 2：书房/客房整理与双段洗烘配送（`study_guest_laundry`） | 任务 3：车库工坊与庭院露台晚间备场（`garage_garden_patio_prep`） |
|---|---|---|---|
| **任务规格路径**（相对 `../experiments/hot_lunch_benchmark_20261113/`） | `inputs/hot_lunch_living_l2/task.json` | `inputs/study_guest_laundry/task.json` | `inputs/garage_garden_patio_prep/task.json` |
| **规格 / 场景 SHA-256** | `spec: e1b3a2c7ed4a...`<br>`scene: a1336ec63ed6...` | `spec: a9ce1aa4463d...`<br>`scene: a0b48b029ef8...` | `spec: d44cddf6d9cb...`<br>`scene: 3447f3cfeff3...` |
| **底层场景与连通区域** | `preparing_lunch_box`（`house_single_floor`）：厨房 + 餐厅 + 客厅 | `clean_up_your_desk`（`house_single_floor`）：南端家政间 + 走廊 + 书房 + 北端客卧 + 客卫（5 扇门） | `spraying_for_bugs`（`house_double_floor_lower`）：西侧车库工坊 + 室内走廊/厨房 + 东侧户外庭院 |
| **跨区单程距离** | 厨房中岛 ↔ 客厅茶几约 **`9.5 m`** | 南端家政间 ↔ 北端客卧套间约 **`25.5 m`** | 西侧车库工作台 ↔ 东侧花园露台约 **`26.0 m`** |
| **后台定时家电过程** | 微波炉加热 2 根生热狗 **`180 s`**（含防烤焦上限） | **双段串联**：洗衣机洗涤 **`180 s`** → 烘干机烘干 **`180 s`**（共 **`360 s`**） | 车库烘干窑炉烘烤劈开的两半木柴 **`360 s`** |
| **可选载具与分批方式** | 厨房中岛方形托盘 `hl2_tray`（`48 × 48 cm`），可一次合运冷餐具与茶瓶去客厅 | 南端家政间敞口藤编衣篮 `sgl_carrier`（`wicker_basket`），可一次合运 **6 件**（烘干袜子/短裤 + 水杯/矿泉水/香皂/卷纸）跨 25.5 m 北运 | 车库主工作台工程托盘 `ggpp_tray`（`54 × 54 cm`），可**去程合运 2 颗灯泡+板刷+喷雾器**、**返程合运板刷+喷雾器归位**、**二趟合运两半干柴** |
| **核心操作与评估器约束** | 开冰箱取生热狗、微波加热、手持餐刀在砧板上将整苹果切成两半摆入双盘、送达双份熟热狗+半苹果+叉子+水杯+茶瓶至客厅茶几、餐刀与砧板收纳进储物高柜并关门、空手结束（**5 个终态组**） | 抹布擦 6 处灰尘（走廊搁板、笔记本、键盘、书桌、客卧衣柜、客卫洗手台）后归位工具桌；两张海报挂墙钉、两本书上搁板、笔袋入走廊双抽屉柜关严、玩具入箱、双台灯摆正关灯；**4 项过程守卫**：先洗后烘、先关笔记本再擦屏后重开、先擦衣柜再放净衣、先擦洗手台再放洗漱品（**4 个终态组 / 37 项顶层子条件**） | 手持斧头劈开整原木为两半（`cut`）入窑炉烘干送露台柴火桌、开炭火烤炉；板刷擦净露台餐桌（`wipe`）；移两盏台灯上餐桌，开金属工具箱取 2 颗灯泡分别装上台灯并点亮（`attach`+`toggle`）；除虫喷雾器给两盆绿植喷药（`spray`）；斧头/板刷/喷雾器归位车库工具台，关工具箱与窑炉，空手结束（**6 个终态组**） |
| **物理反例验证记录** | 构建初态 21 项断言全过（`runs/build_l2_r1`） | 漏做房间整理反例被评估器拦截（`runs/neg_sgl_omit_room_tidy_r1`，`exit 0`） | 漏喷药、漏关窑炉、漏送干柴 3 条物理反例均被拦截（`runs/neg_ggpp_omit_*_r1`，全部 `exit 0`） |

---

## 三、强模型基线回合实验配置（完整参数与复现方式）

为保证三项任务的强模型基线完全可比，所有模型回合均固定以下配置：

### 3.1 实验配置明细表

| 配置类别 | 参数项 | 取值 | 说明 |
|---|---|---|---|
| **模型与客户端** | 模型名称 | `gpt-6.1-sol` | 项目默认强模型 |
| | 推理强度 | `--effort low` | 低推理档位基线 |
| | 客户端与服务配置 | `codex-cli 0.166.0`，`--model-provider-profile ccswitch` | 本机运行控制器与 MCP 代理，SSH 端口转发连接远端仿真器；网关限流自动重连 `--rate-limit-resumes 6 --resume-backoff-seconds 120` |
| **观测与工具暴露** | 工具暴露方式 | `--mcp-tool-exposure direct` | 工具直接暴露给模型，不走客户端代码模式包装；每次返回 `front`/`left`/`right`/`back` 四路 `512×512` RGB 原图 |
| | 上下文策略 | `--codex-context client_default` | 保留客户端默认完整多轮上下文（不做历史图片裁剪或外部状态账本替换） |
| | 暴露工具集 | `initialize`, `look`, `act`, `list_skills`, `read_skill`, `finish`（共 6 个） | `--profile basic-skills --goal-skill none`：**不启用** `task_notes`、`goal-decomposition` 或 `transport_advice` |
| **Harness 护栏开关** | 完成声明复核 | `--finish-guard on` | 步数剩余充足时退回首次 `achieved` 声明（以及非连续阻塞下的过早 `aborted`），要求结合最新 RGB 核实一次 |
| | 未结回合续跑提醒 | `--unfinished-turn-nudges 2` | 若模型未调用 `finish` 即停止工具调用，在同一会话线程内发送中性续跑提醒 |
| | 自主执行说明 | `--autonomy-instruction on` | 在系统提示词末尾附加“回合期间无人回复，停止调工具只会暂停机器人，请持续执行直至完工” |
| | 提示词与技能哈希 | `system_prompt_sha256`: `0d245c7f796fd95dbbeda81b8ea07e94689ba53c20328e309fe353031ff63c2a`<br>`skills_sha256`: `34fa018e589338672d2afb6f180f8228f01655a7997f16a9263acd5ae58974e7` | 三项任务逐字节完全一致。注：上游 `skills/pick-and-place/SKILL.md` 含一句通用载具常识说明（共线多物可对比单趟与托盘/容器合运），三回合使用同一份技能树 |
| **预算与硬件隔离** | 步数与墙钟上限 | `--max-agent-steps 400`，`--timeout 0` | 最多 400 次 `act`/`look` 步，无墙钟硬超时 |
| | GPU 与并发隔离 | `workstation-master-zx` GPU 0：`NVIDIA RTX PRO 6000 Blackwell Server Edition`（`97,214 MiB` 显存，`GPU-82875626-2b46-d877-466e-74fadb85959b`） | 全程通过阻塞文件锁 `/data/zx/sunyujie/robot_manipulation/experiments/sunyujie_simulator_gpu0_slot0.lock` 独占 0 号卡串行运行（本机 GPU 零占用，远端 0 号卡零并行仿真干扰） |
| **冻结源码版本** | 任务 1 模型回合 | `source_carrier_latch_6ee76ea`（提交 `6ee76ea6c594`，归档 SHA-256 `25f991e20457...`） | 任务 1 并行构建完成时冻结版本；后续在 `a94f4ed` 上重跑任务 1 双参考确认指标无变化（`618 s` vs `622 s`） |
| | 任务 2 / 3 模型回合 | `source_flush_support_a267904`（提交 `a267904f2d04`，归档 SHA-256 `ec3c76fbf404...`） | 含全部容器内部放置净空与齐平支撑修复的最新冻结版本 |

### 3.2 复现命令（场景构建、特权参考、物理反例、模型回合、指标汇总）

在工作目录 `/mnt/data/home/sunyujie/robot_manipulation/experiments/hot_lunch_benchmark_20261113` 下：

```bash
# 1. 远端 PRO 6000 场景构建（自动加 GPU 0 文件锁）
SRC_NAME=source_flush_support_a267904 ./run_build_pro6000.sh inputs/<task_id>/recipe.json build_<name>

# 2. 远端 PRO 6000 特权参考流程验收
SRC_NAME=source_flush_support_a267904 ./run_ref_pro6000.sh inputs/<task_id>/task.json <order_name> ref_<name>

# 3. 远端 PRO 6000 物理反例验收
SRC_NAME=source_flush_support_a267904 ./run_neg_pro6000.sh inputs/<task_id>/task.json <negative_case_id> neg_<name>

# 4. 强模型基线回合（必须使用含 mcp SDK 的 behavior Python 解释器）
/mnt/data/home/sunyujie/miniforge3/envs/behavior/bin/python ./run_model_episode.py \
  <episode_name> \
  --source source_flush_support_a267904 \
  --task <hot_lunch_living_l2 | study_guest_laundry | garage_garden_patio_prep> \
  --profile basic-skills \
  --goal-skill none \
  --model gpt-6.1-sol \
  --effort low \
  --max-agent-steps 400 \
  --gpu 0 \
  --finish-guard on \
  --autonomy-instruction on

# 5. 六项效率指标汇总表导出
PYTHONPATH=source_flush_support_a267904/src \
  /mnt/data/home/sunyujie/miniforge3/envs/behavior/bin/python \
  source_flush_support_a267904/scripts/report_efficiency_table.py \
  "标签=运行目录" ... --json <out_new.json>
```

---

## 四、六项效率指标口径、Token 核验注意事项与完整结果总表

### 4.1 六项指标统一口径与多轮续跑 Token 统计注意事项

1. **完成率**：由独立评估器严格判定（`run.json` 中 `task_success`），差任意 1 项子条件即判 `否`；同时单独核查控制器正式结束（`formal_finish_observed = true`）与录像完整性（`video.json` 中 `status = "passed"`，三回合全部满足）。
2. **步骤数（失败）**：模型被接纳的 `act` / `look` 总调用数，括号内为返回 `ok = false` 的失败步数。
3. **机器人时间（路程 + 操作 + 等待）**：由 `robot_clock.jsonl` 精确累加。
4. **墙钟时间（模型 + 工具）**：回合总墙钟秒数，拆分为模型侧推理/通信墙钟与仿真器工具执行墙钟（不含约 2–4.7 分钟的 OmniGibson 启动加载耗时）。
5. **轨迹长度**：严格取 `navigation_plans.jsonl` 中原生 A* 承诺可行走路径长度之和（`travel_distance_m`），**不使用**起止点欧氏位移 `ideal_navigation_distance_m`。
6. **Token 用量（输入 / 其中缓存命中 / 输出）与多轮续跑核验提醒**：
   - **重要发现**：当同一会话（同一 `thread_id`）因网关并发限流或空回复触发多次 `codex exec resume` 续接时（如任务 3 续接了 27 次），`controller/model_events.jsonl` 中每次输出的 `turn.completed.usage` 是**从第 0 轮起算的整段会话累计值**，而非当轮增量！若直接把多条 `turn.completed.usage` 相加，在任务 3 上会将真实的 `22,881,171` 输入 token 重复累加放大 10.01 倍变成 `229,109,229`（当前 `reporting/efficiency_table.py` 待提交补丁修复该多轮累加逻辑）。
   - 下表及 `three_tasks_model_vs_ref_metrics.json` 中的全部 token 数字均已通过 `controller.json` 记录的 `session_sha256` 逐字节绑定核验 `~/.codex/sessions/2026/11/13/rollout-*.jsonl`，确保 `sum(last_token_usage) == final_total_token_usage` 零误差（核验文件归档在各回合 `controller/native_session_token_usage.json`）。

### 4.2 三项新任务 + 厨房整理版历史基线：六项效率指标同表汇总

| 任务 | 运行配置（中文描述 + 相对实验目录路径） | 执行器冻结版本 | 模型与推理档位 | 完成 | 步骤数（失败） | 机器人时间 s（路程 + 操作 + 等待） | 墙钟时间 s（模型 + 工具） | 轨迹长度 m | token 数（输入 / 其中缓存 / 输出） |
|---|---|---|---|---|---:|---:|---:|---:|---:|
| **任务 0：厨房整理版**（对照锚点） | 参考·顺序 1（`kitchen_v2_tidy` / `order1_r2_snap2m`） | `source_floorsnap2m_55dd0ff` | 无（特权参考） | 是 | 200（0） | **2,449**（1,329 + 1,100 + 20） | 774（— + 420） | **420.1** | — |
| | 参考·顺序 2（`kitchen_v2_tidy` / `order2_r2_snap2m`） | `source_floorsnap2m_55dd0ff` | 无（特权参考） | 是 | 203（0） | **2,578**（1,458 + 1,100 + 20） | 724（— + 420） | **460.8** | — |
| **任务 1：双人热午餐 L2** | 参考·最优并行+托盘分批（`runs/ref_l2_optimal_r2`） | `source_carrier_latch_6ee76ea` | 无（特权参考） | 是 | 66（0） | **622**（192 + 430 + 0） | 389（— + 104） | **53.9** | — |
| | 参考·最优并行+托盘分批·复核（`runs/ref_l2_optimal_r3`） | `source_inside_resting_a94f4ed` | 无（特权参考） | 是 | 65（0） | **618**（188 + 430 + 0） | 382（— + 105） | **54.1** | — |
| | 参考·习惯顺序+单件往返（`runs/ref_l2_habit_r2`） | `source_inside_resting_a94f4ed` | 无（特权参考） | 是 | 73（0） | **918**（328 + 410 + 180） | 382（— + 105） | **90.2** | — |
| | **强模型·基线设施回合**（`episodes/sol_l2_basic_fg_auto_low_r1`） | `source_carrier_latch_6ee76ea` | **`gpt-6.1-sol`（`low`）** | **是** | **73（2）** | **1,119**（359 + 550 + **210**） | **2,288**（2,094 + 194） | **111.9** | **6,890,950 / 6,687,104 / 5,159** |
| **任务 2：书房/客房洗烘配送** | 参考·最优双段并行+洗衣篮分批（`runs/ref_sgl_optimal_r4`） | `source_flush_support_a267904` | 无（特权参考） | 是 | 130（0） | **1,465**（685 + 780 + 0） | 512（— + 225） | **234.6** | — |
| | 参考·习惯顺序干等+单件往返（`runs/ref_sgl_habit_r2`） | `source_flush_support_a267904` | 无（特权参考） | 是 | 121（0） | **2,273**（1,213 + 700 + 360） | 477（— + 192） | **476.5** | — |
| | **强模型·基线设施回合**（`episodes/sol_sgl_basic_fg_auto_low_r2`） | `source_flush_support_a267904` | **`gpt-6.1-sol`（`low`）** | **否** *(36/37)* | **252（20）** | **2,989**（1,259 + 1,250 + **480**） | **6,828**（6,366 + 462） | **428.4** | **31,434,783 / 30,834,176 / 20,284** |
| **任务 3：车库工坊与庭院备场** | 参考·最优窑炉并行+双向托盘分批（`runs/ref_ggpp_optimal_r2`） | `source_door_clear_682df4b` | 无（特权参考） | 是 | 94（0） | **1,121**（551 + 570 + 0） | 200（— + 63） | **179.8** | — |
| | 参考·习惯顺序干等+单件往返（`runs/ref_ggpp_habit_r1`） | `source_door_clear_682df4b` | 无（特权参考） | 是 | 72（0） | **1,936**（1,186 + 390 + 360） | 220（— + 83） | **477.2** | — |
| | **强模型·基线设施回合**（`episodes/sol_ggpp_basic_fg_auto_low_r1`） | `source_flush_support_a267904` | **`gpt-6.1-sol`（`low`）** | **是** | **165（22）** | **2,232**（1,192 + 800 + **240**） | **5,721**（5,500 + 222） | **409.6** | **22,881,171 / 22,476,288 / 12,577** |

> **为什么不能只看步骤数**：在任务 2 与任务 3 中，使用藤编衣篮或工程托盘装卸物品使最优参考流程比单件直搬的习惯参考**多出 9 步（130 vs 121）和 22 步（94 vs 72）**，但**真实机器人时间分别净省 808 秒（-35.5%）与 815 秒（-42.1%），跨区行走路程分别砍掉 241.9 米（-50.8%）与 297.4 米（-62.3%）**。

---

## 五、三项任务强模型基线回合行为诊断与损耗归因

### 5.1 任务 1：`sol_l2_basic_fg_auto_low_r1`（100% 完成，慢 +81.1%，多走 +106.8%）
- **加热原地空等 `210 s`**：第 1–19 步将两根生热狗放入微波炉开机后，连续调用 `wait` 四次（`30 + 30 + 30 + 120 = 210 s`）站在微波炉前干等提示音，期间未并行切苹果、收纳刀板或搬运冷餐具。
- **0 次触碰托盘、单件跑 7 趟**：尽管开局第 2 次 `read_skill` 读取了 `pick-and-place/SKILL.md` 且中岛摆有醒目的 `48 × 48 cm` 方形托盘，第 51–72 步仍分 **7 趟**在厨房中岛与客厅茶几之间逐件搬运两个餐盘、茶瓶、两只杯子与两把餐叉，并在结束理由中写明 `"Tray remains on island, optional for serving."`。
- **动作鲁棒性**：全回合 73 步仅 2 次失败（1 次 `look` 角度越界预检拒绝、1 次放砧板入柜偏低当场重抓恢复），单轮 81 次 API 调用到底。

### 5.2 任务 2：`sol_sgl_basic_fg_auto_low_r2`（36/37 子条件完成、4/4 守卫零违例，空等 480 s）
- **受初始位置支配的晚开洗衣机，原地空等 `480 s`**：模型出生在北端书房，没有先南下家政间启动耗时最长的洗烘，而是先花第 1–107 步（`robot_clock = 0 → 1,193.7 s`）把北端书房、走廊、客卧、客卫全部整理擦拭完毕，第 114 步（`1,266.3 s`）才开启洗衣机，此时全屋已无其他活可干，只能在洗衣机前 `wait(120s)×2 = 240 s`、烘干机前 `wait(120s)×2 = 240 s` 原地死等。
- **`25.5 m` 长跨度激活了载具满载北运**：与任务 1（`9.5 m`）不用托盘相反，在 `25.5 m` 走廊压力下，模型主动直接点选藤编衣篮 `sgl_carrier` **10 次**（6 次 `place_inside` 装篮 + 4 次 `grasp` 抓握，配对 4 次 `place_on_top`），将烘干的袜子、短裤与家政间台面的水、杯、卷纸、香皂**一次性装满 6 件单趟北运至客卧边桌**（单件跨屋往返 0 趟）。
- **无状态账本导致的探索与复检空跑（428.4 m 路程来源）**：① 前期未知房间拓扑，沿走廊逐扇推门寻找抹布与家政间；② 第 191–209 步模型把各房间门与柜门全关上后首次调 `finish(achieved)`，被 `--finish-guard on` 退回要求核实最新 RGB 画面后，因没有外部容器状态账本，模型在第 210–252 步又从南端走穿全屋，**把已关好的走廊抽屉、书房门、客卧衣柜、客卫推拉门逐一重新打开拍照再关上（耗费 43 步、近百米）**。
- **唯一未过项根因（`not desk_dust`）**：`desk_bhkhxo_0` 原生材质为暗橄榄绿色，表面 `dust` 在 `512×512` 画面中仅为极细微斑点；第 65 步手持抹布调用 `wipe` 的落点 `[0.571, 0.674]` 偏出书桌前缘下方 8 个竖直像素打中地板 `floors_qnvzkr_0`，执行器返回 `no_removable_material`（预检拒绝，未计 10s）但未回显实际命中的物体类别（`floors` vs `desk`），模型遂误判书桌已干净。其余 4 次 `postcondition_error` 放置越界均在 1–2 步内自主恢复。

### 5.3 任务 3：`sol_ggpp_basic_fg_auto_low_r1`（100% 完成，慢 +99.1%，多走 +127.8%）
- **“前 240s 死等 + 后 120s 自发并行”**：模型出生在车库工作台旁，第 1–15 步（`170.4 s`）即用斧头劈开原木装入烘干窑炉开机；随后先原地 `wait(120s)×2` 空等 `240 s`，见仍未烘完后不再傻等，第 18 步拿板刷跨 `26 m` 去露台擦餐桌、开炭火烤炉（木柴在第 26 步 `544.3 s` 后台完成烘干）。
- **局部会用载具、缺乏跨品类合单（跑了 4 个来回 vs 最优 2 个来回）**：出程运两半干柴时主动叠上工程托盘 `ggpp_tray` 一趟送出（交互 6 次），运两颗小灯泡时灵巧地直接拎走整只闭合金属工具箱 `ggpp_toolbox` 到露台开箱装灯，**但没有在开局烤柴后把“板刷 + 喷雾器 + 灯泡”一次合装托盘带出，返程也未用托盘收工具**（板刷与喷雾器单件往返），外加 `finish-guard` 退回后跨 26 m 返车库复检 1 趟，累计行走 `409.6 m`（多走 `229.8 m`）。
- **结束保护救活回合与细高物体重试**：第 11 步装第 2 块木柴擦碰窑口滑落连续失败 3 次后，模型试图调 `finish(aborted)` 放弃，被 `--finish-guard on` 退回后重抓成功并最终满分通关；全回合 22 个失败步中，有 7 次集中在细高台灯 `ggpp_lamp1` 放到露台餐桌上的沉降侧倒重试（最终稳定卧于桌面并装泡点亮）。

---

## 六、阶段 1b 四维覆盖扩展预备进度与文件索引

除上述已完成模型基线闭环的 3 项带家电计时器任务外，为回答“**没有家电计时器时，纯空间载具路由与地点记忆是否有显著收益**”并补齐卧室（2/2）、书房（2/2）、吸尘清洁（`vacuum`）与半未知跨房找物维度，已完成**任务 4「二层双卧室与中央书房跨房载具巡回分拣及吸尘清洁」（`upstairs_study_bedrooms_exchange`）**的静态配方与二轮 CPU 可通行图/几何余量审校（真实 A* 下 `0 s` 等待，最优双向托盘巡回 `80 步 / 891.8 s / 114.8 m` vs 无绕路习惯顺序 `83 步 / 1,150.0 s [+29.0%] / 261.2 m [+127.6%]`），待排期上卡完成物理构建验收。

### 完整实验产物索引（均位于 `/mnt/data/home/sunyujie/robot_manipulation/experiments/hot_lunch_benchmark_20261113/`）

| 类别 | 相对路径 | 内容说明 |
|---|---|---|
| 机器可读指标汇总 | `three_tasks_model_vs_ref_metrics.json` | 3 项任务共 9 行（最优参考、习惯参考、`gpt-6.1-sol/low` 回合）完整六项指标与原生会话 SHA-256 凭证 |
| 任务 1 输入与运行 | `inputs/hot_lunch_living_l2/`<br>`runs/ref_l2_optimal_r2`, `r3` / `runs/ref_l2_habit_r1`, `r2`<br>`episodes/sol_l2_basic_fg_auto_low_r1/`（含 `NOTES.md`） | 双人热午餐 L2 冻结场景、双参考运行、强模型回合及复盘 |
| 任务 2 输入与运行 | `inputs/study_guest_laundry/`（历史版 `r1`, `r2` 保留）<br>`runs/ref_sgl_optimal_r4` / `runs/ref_sgl_habit_r2` / `runs/neg_sgl_omit_room_tidy_r1`<br>`episodes/sol_sgl_basic_fg_auto_low_r2/`（含 `NOTES.md`；0 步预检目录 `r1` 保留） | 书房/客房洗烘配送最终冻结场景、双参考与反例、强模型回合及复盘 |
| 任务 3 输入与运行 | `inputs/garage_garden_patio_prep/`<br>`runs/ref_ggpp_optimal_r2` / `runs/ref_ggpp_habit_r1` / `runs/neg_ggpp_omit_*_r1`<br>`episodes/sol_ggpp_basic_fg_auto_low_r1/`（含 `NOTES.md`） | 车库工坊与庭院备场冻结场景、双参考与 3 条反例、强模型回合及复盘 |
| 任务 4/5 扩展设计 | `TASK4_5_COVERAGE_AND_DESIGN.md`<br>`inputs/upstairs_study_bedrooms_exchange/{make_recipe.py,recipe.json}` | 四维多样性覆盖矩阵、CPU 执行器 `fold`/`vacuum` 审计、任务 4 静态配方与任务 5 蓝图 |
| 冻结源码副本 | `source_timedproc_2ac2c72/`、`source_carrier_latch_6ee76ea/`、`source_door_clear_682df4b/`、`source_inside_resting_a94f4ed/`、`source_flush_support_a267904/` | 各阶段实验对应的完整源码快照与 `source_freeze.json` 哈希清单 |
