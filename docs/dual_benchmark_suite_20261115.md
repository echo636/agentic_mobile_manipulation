# 双层长程具身评测基准规范与验收报告（2026-11-15）

本文档定义用于评测**“底层通用具身闭环感知记忆 Harness（路线 A）+ 上层零领域假设通用前置条件与时空代价图规划器（路线 B）”**的统一双层固定评测基准（机器可读配置见 [dual_benchmark_suite_20261115.json](../task_designs/dual_benchmark_suite_20261115.json)）。

> [!IMPORTANT]
> **Benchmark（试卷）与 Harness（考生）严格隔离原则**
> - **Benchmark 侧（对所有模型与方法 100% 固定不变）**：任务定义、冻结初始场景快照、原子动作物理执行器、客观事实回执、独立评估器与六项核心指标计量逻辑。
> - **Harness 侧（三级消融对照项，默认全部关闭）**：四向局部放大回显、公开因果受阻提示、外部空间/容器/手部状态记忆压缩（`--state-memory`）、滚动新会话续跑（`--rolling-handoff-steps`）、记忆感知结束前核验（`--finish-guard`）以及零领域假设通用任务图规划器（`--planning-mode task_graph`）。

---

## 一、三级通用对照阶梯与六项同表汇报规范

### 1.1 三级考生阶梯（零代号标准配置名与精确开关）

| 阶梯档位 | 描述性中文配置名称 | 仿真端 `vision_cli.py` 开关 | 代理端 `mcp_server.py` 开关 | 控制端 `run_codex_controller.py` 开关 |
|---|---|---|---|---|
| **第一档** | **裸模型最小基线**（仅 4 个基础原子工具，无操作护栏、无外部记忆、无规划器） | `--agent-profile minimal --goal-skill none --auxiliary-feedback off --state-memory off --planning-mode off` | `--finish-guard off --state-memory off --planning-mode off`* | `--agent-profile minimal --goal-skill none --autonomy-instruction off --unfinished-turn-nudges 0 --state-memory off --rolling-handoff-steps 0 --planning-mode off` |
| **第二档** | **+ 通用具身闭环感知与记忆设施（路线 A）**（四向视觉锚定放大回显 + 外部状态记忆压缩 + 预期偏差自纠错 + 记忆感知结束核验） | `--agent-profile skills --goal-skill hierarchical --auxiliary-feedback on --state-memory on --planning-mode off` | `--finish-guard on --state-memory on --planning-mode off`* | `--agent-profile skills --goal-skill hierarchical --autonomy-instruction on --unfinished-turn-nudges 2 --state-memory on --rolling-handoff-steps 80 --planning-mode off` |
| **第三档** | **+ 零领域假设通用前置条件与时空代价图规划器（路线 A + 路线 B 结合）**（底层通用感知记忆 Harness + 上层 `plan_task_graph` 通用代价图规划器） | `--agent-profile skills --goal-skill hierarchical --auxiliary-feedback on --state-memory on --planning-mode task_graph` | `--finish-guard on --state-memory on --planning-mode task_graph`* | `--agent-profile skills --goal-skill hierarchical --autonomy-instruction on --unfinished-turn-nudges 2 --state-memory on --rolling-handoff-steps 80 --planning-mode task_graph` |

*\*注：当评测客户端为 `--client agy`（Antigravity CLI）时，三档在 `mcp_server.py` 上统一附加 `--max-inline-image-bytes 9600`，避免客户端将 `>10 KB` 全景图离线转存导致模型失明（对坐标几何无任何改变）。*

### 1.2 六项核心效率与完成度同表指标

所有实验表严格区分**强模型**（`gpt-6.1-sol`、`gemini-4-hydrogen-low`）与**弱模型**（`gpt-6.1-terra`、`gemini-3.8-flash-low`），每一行同时报告以下六项指标（比较效率时完成率不得下降）：

1. **完成率与子目标达成比例**：独立评估器 `evaluation.task_success` 与 `evaluation.goal_satisfaction_fraction`；
2. **步骤数**：已接纳动作/观察步数 `agent_steps_admitted`（括号注明失败步数 `failed_actions`）；
3. **机器人时间（s）**：`robot_time.total_seconds`（同时拆解 `travel_seconds + manipulation_seconds + wait_seconds`）；
4. **轨迹长度（m）**：底层原生 A* 实际行走路径累计长度 `robot_time.travel_distance_m`；
5. **墙钟时间（s）**：回合端到端耗时 `wall_seconds`（拆解为模型思考 `model_wall_seconds` + 仿真工具执行 `tool_wall_seconds`）；
6. **Token 消耗**：`input_tokens / cached_input_tokens / output_tokens`（跨滚动续跑分段无损累加）。

---

## 二、第一层试卷：原版 BEHAVIOR-1K 长程任务集（验证零定制普适性）

依据 [experiments/benchmark_inventory_20261112/INVENTORY.md](../../experiments/benchmark_inventory_20261112/INVENTORY.md) 盘点结论与 `catalog_snapshot.json` 物理可行性审计记录，从原版 BEHAVIOR-1K 官方公开任务集中选出 **4 项无液体/布料物理死卡陷阱、已在当前 RGB 执行器上跑通满分参考证据的代表性多房间/多阶段长程任务**，全部使用**官方原始实例 `instance 301, seed 0`、官方原始 BDDL 评估器与未改动的官方英文指令**；同时纳入已完成双顺序验收的 **200 步超长压力锚点任务 `kitchen_v2_tidy`**：

| 编号 | 任务 ID（场景模型） | 空间跨度与参考步数 | 核心长程与规划考察点 | 官方 BDDL / 实例 301 SHA-256 前缀 | 建议步数上限 |
|---|---|---|---|---|---|
| **B1K-1** | `putting_up_Christmas_decorations_inside`<br>(`house_single_floor`) | 客厅 ↔ 餐厅<br>（审计参考 `30` 步） | 从柳条篮取出 6 件装饰物 + 搬运 3 个礼盒，满足“同沙发配对”“同餐桌配对”与圣诞树旁归集，考察跨房分批与尾段防漏件核验 | BDDL: `10cf6f3d...`<br>Inst301: `01bad5a1...` | `120` |
| **B1K-2** | `assembling_gift_baskets`<br>(`house_double_floor_lower`) | 客厅多工位往返<br>（审计参考 `57` 步） | 将桌上 4 类共 **16 件**物品按每篮各 1 件精确配入地面 **4 只**礼篮（同时入选官方挑战集与协作者 20 项长任务清单），直接检验外部容器记忆与防重防漏核验 | BDDL: `6e6c82ba...`<br>Inst301: `bb1f0710...` | `160` |
| **B1K-3** | `loading_the_car`<br>(`house_double_floor_lower`) | 客厅 ↔ 走廊 ↔ 车库<br>（审计参考 `45` 步） | 先将桌上数码相机装入地面收纳容器，再跨房将容器与网球拍送入车库汽车后备箱并关盖，原生检验层级载具打包与长距离跨区搬运 | BDDL: `c5efe2cb...`<br>Inst301: `9989bfb4...` | `120` |
| **B1K-4** | `slicing_vegetables`<br>(`house_single_floor`) | 厨房多工位<br>（审计参考 `39` 步） | 开冰箱取出 3 类共 **5 件**蔬菜、归集至砧板、持削皮刀连续切丁并关闭冰箱门，考察闭门容器取物、单手工具持握批处理顺序与终态闭门 | BDDL: `783060c4...`<br>Inst301: `038fad14...` | `140` |
| **B1K-S1**<br>*（超长锚点）* | `kitchen_v2_tidy`<br>(`house_single_floor` 整理版) | 厨房 ↔ 餐厅全流程<br>（双参考 `200–203` 步，`418.5–459.6 m`） | 11 阶段双人热餐备餐、切配、微波加热、留餐封装冷藏、擦洗与扫地；已在 PRO 6000 通过两种合法顺序完整物理验收，专用于压测超百步记忆压缩与滚动续跑 | Task: `32cc0527...`<br>Scene: `df5546a9...` | `320` |

---

## 三、第二层试卷：多模式自建长程规划基准（`scheduling_v1`，扩建至 T1–T7）

第二层试卷覆盖 7 类通用物理规划模式（`M1` 有限容量载具批处理、`M2` 异步计时过程等待重叠、`M3` 多房间双向回程配货、`M4` 单调状态/最少重置排序、`M5` 闭柜未知存货条件盘点与盈余退货代价、`M6` 多跳受阻容器/工具前置解锁、`M7` 有限缓冲区非单调无死锁换位），其中 **T4–T7 为 `0.0 s` 等待的纯空间、状态与条件规划难任务**：

| 编号 | 任务 ID 与中文名 | 设备等待 | 主导规划模式 | 最优特权参考（机器人时间 / 路程 / 等待） | 习惯特权参考（机器人时间 / 路程 / 等待） | 最优相对习惯节省幅度 |
|---|---|---:|---|---|---|---|
| **T1** | `hot_lunch_living_l2`<br>双人热午餐跨房备餐与摆台 | `180 s` | M1 + M2 | `618.1 s` / `54.1 m` / `0.0 s` | `918.4 s` / `90.2 m` / `180.0 s` | **省时 `32.7%` · 省路 `40.0%`** |
| **T2** | `study_guest_laundry`<br>书房客房整理与双段串联洗烘 | `540 s` | M1 + M2（串联） | `1,465.2 s` / `234.6 m` / `0.0 s` | `2,273.1 s` / `476.5 m` / `480.0 s` | **省时 `35.5%` · 省路 `50.8%`** |
| **T3** | `garage_garden_patio_prep`<br>车库工坊与庭院露台备场 | `240 s` | M1 + M2 + M6 | `1,120.8 s` / `179.8 m` / `0.0 s` | `1,935.6 s` / `477.2 m` / `240.0 s` | **省时 `42.1%` · 省路 `62.3%`** |
| **T4** | `upstairs_study_bedrooms_exchange`<br>二楼三房 4 换 4 双向置换清洁 | **`0 s`** | M1 + M3 | `894.1 s` / `134.8 m` / `0.0 s` | `1,040.9 s` / `262.9 m` / `0.0 s` | **省时 `14.1%` · 省路 `48.7%`** |
| **T5** | `kitchen_dining_cleanliness_restock`<br>餐厨防污染单调清洁与闭柜补货 | **`0 s`** | M1 + M4 + M5 | `501.8 s` / `60.9 m` / `0.0 s` | `488.8 s` / `92.6 m` / `0.0 s` | **省路 `34.2%`**（仅洗 1 次抹布） |
| **T6** | `tabletop_buffer_nonmonotonic_swap`<br>有限缓冲台面非单调借位换位 | **`0 s`** | M1 + M7 | `418.4 s` / `33.3 m` / `0.0 s` | `527.0 s` / `146.5 m` / `0.0 s`（盲移易死锁） | **防死锁分水岭 · 省路 `77.3%`** |
| **T7** | `blind_cabinet_exact_restock_unlock`<br>**闭柜精确配平与压盖工具箱解锁（新增）** | **`0 s`** | **M1 + M5 + M6** | *见第四节 PRO 6000 实测表* | *见第四节 PRO 6000 实测表* | **省时 `>= 20%` · 省路 `>= 35%`** |

---

## 四、新增零等待任务 T7（`blind_cabinet_exact_restock_unlock`）构造与 PRO 6000 物理验收

### 4.1 场景结构与为什么必须“先北端盘点 + 顺路压盖解锁 + 精确托盘配货”

- **底座与跨区尺度**：基于 `clean_up_your_desk`（`house_single_floor`），北端客房（`cabinet_east`、`cabinet_west`、`guest_lamp`、`guest_table`）与南端杂物间补给台（`utility_counter`、`carrier`）之间**单程原生 A* 路径实测为 `24.0–25.5 m`**（满足 `>= 15 m` 跨区硬指标）。
- **闭柜未知存货精确配平 + 盈余退货惩罚（M5）**：
  - 初始北端两座闭门储物柜内暗藏存货：`cabinet_east` 已有 `1 水 + 1 饼干`（仅缺 `1 苹果汁`），`cabinet_west` 已有 `1 苹果汁`（缺 `1 水 + 1 饼干`）；
  - 南端杂物间台面摆有 `2 水 + 2 苹果汁 + 2 饼干`（共 6 件，含 3 件盈余）；
  - 评估器采用对称三划分公式 `one_of_three_partition`：要求每类 3 个同款实例终态严格满足“东柜内 1 件、西柜内 1 件、南端杂物台面 1 件，且其余实例均不在两座柜内”。不先开柜盘点而从南端盲搬多余物资北上，必然触发额外 `~50 m` 南北折返退货。
- **压盖金属工具箱四跳解锁（M6）**：
  - 东走廊工作台 `tool_bench` 上的闭合金属工具箱 `toolbox` 内锁有两枚灯泡（`bulb1`、`bulb2`），箱旁/台面放有两本平装书（`book1`、`book2`）；
  - 转换守卫 `toolbox_open_requires_books_cleared` 强制要求在打开 `toolbox` 前必须将两本书全部移离工作台与工具箱（上架至书房门外唯一墙架 `hall_shelf`），随后方可开箱取泡、关箱，并分别 `attach` 及 `toggle_on` 点亮书房与客房两盏台灯。

### 4.2 远端 RTX PRO 6000 双参考流程与四项物理负例实测汇总

*(场景 `build_t7_bceru_r2`；冻结契约 `task.json` SHA-256 `23f559b2...`；六个验证 `*_r5` 均在远端 PRO 6000 上用同一份冻结文件实测，原始记录在 `experiments/route_ab_team_20261115/runs/`。`r3`/`r4` 为最优参考流程动作顺序修正过程：开箱时托盘在工作台会把工具箱顶落、最后一步误装 `bulb1`，均已改正)*

| 验证项 | 模式 / 阶段序列 | 独立评估器 `task_success` | 终态满足率 | 守卫违规 | 机器人总时间（路程 + 操作 + 等待） | 原生 A* 总路程 | 对比结论 |
|---|---|---|---:|---|---:|---:|---|
| `ref_t7_optimal_r5` | `optimal_inspect_unlock_carrier_loop`（先开北柜盘点 → 南下顺路上架双书 → 南端托盘精确装 3 件 → 走廊开箱取双泡点亮书房灯 → 北上补齐两柜点亮客房灯并关柜） | **`True`** | `1.000` (`9/9`) | 无 (`[]`) | `667.4 s` | `114.9 m` | **基准最优（`0.0 s` 等待）** |
| `ref_t7_habit_r5` | `habit_blind_overfetch_sequential`（盲去南端搬货北上含盈余 → 开北柜发现存货补齐并关门 → 南返退回盈余水瓶 → 逐件上架双书与双灯装配） | **`True`** | `1.000` (`9/9`) | 无 (`[]`) | `1146.0 s` | `332.7 m` | **最优省时 `41.8%` · 省路 `65.5%`** |
| `neg_t7_book_guard_r5` | `violate_toolbox_book_guard`（未移走平装书直接打开工具箱） | **`False`** | `0.8` | `['toolbox_open_requires_books_cleared']` | `582.9 s` | `99.0 m` | ✅ 转换守卫与终态双重拦截通过 |
| `neg_t7_omit_restock_r5` | `omit_cabinet_restock`（漏做北柜精确配平） | **`False`** | `0.6` | 无 | `549.7 s` | `111.0 m` | ✅ 精确配平终态拦截通过 |
| `neg_t7_omit_lamp_r5` | `omit_guest_lamp_lighting`（漏装/漏开北端客房台灯） | **`False`** | `0.9` | 无 | `610.7 s` | `106.7 m` | ✅ 灯泡装配与点亮终态拦截通过 |
| `neg_t7_omit_close_r5` | `omit_cabinet_doors_closing`（配平后未关北端柜门） | **`False`** | `0.9` | 无 | `629.5 s` | `110.2 m` | ✅ 柜门闭合终态拦截通过 |

---

## 五、直接复现与评测启动命令模板

### 5.1 原版 BEHAVIOR-1K 任务（以 `assembling_gift_baskets` 第三档路线 A+B 为例）

```bash
# 1. 远端 PRO 6000（获取 GPU 槽位锁且余量 >= 18000 MiB 后启动仿真端）
python -m manipulation_agent.vision_cli \
  --backend omnigibson --policy serve \
  --task assembling_gift_baskets --instance 301 --seed 0 \
  --max-agent-steps 160 --port 29440 \
  --agent-profile skills --goal-skill hierarchical \
  --auxiliary-feedback on --state-memory on --planning-mode task_graph \
  --output /path/to/episode/run

# 2. 本机启动控制器（通过 SSH 隧道连接远端 29440 端口）
python scripts/run_codex_controller.py \
  --client codex --model gpt-6.1-sol --reasoning-effort low \
  --instruction "$(jq -r '.tasks.assembling_gift_baskets.instruction' src/manipulation_agent/tasks.json)" \
  --agent-profile skills --goal-skill hierarchical \
  --autonomy-instruction on --unfinished-turn-nudges 2 \
  --state-memory on --rolling-handoff-steps 80 --planning-mode task_graph \
  --mcp-command python \
  --mcp-args-json '["-m","manipulation_agent.mcp_server","--endpoint","http://127.0.0.1:29440","--finish-guard","on","--state-memory","on","--planning-mode","task_graph"]' \
  --timeout 0 --output /path/to/episode/controller
```

### 5.2 自建多模式基准任务（以新增零等待任务 T7 `blind_cabinet_exact_restock_unlock` 为例）

```bash
# 远端 PRO 6000 将 --task 替换为 --custom-task 指向仓库外 inputs 目录下的 task.json（与 initial_scene.json 同目录）
python -m manipulation_agent.vision_cli \
  --backend omnigibson --policy serve \
  --custom-task /data/zx/sunyujie/robot_manipulation/experiments/route_ab_team_20261115/inputs/blind_cabinet_exact_restock_unlock/task.json \
  --max-agent-steps 160 --port 29440 \
  --agent-profile skills --goal-skill hierarchical \
  --auxiliary-feedback on --state-memory on --planning-mode task_graph \
  --output /path/to/episode/run
```
