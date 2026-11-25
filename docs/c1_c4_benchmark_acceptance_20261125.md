# C1–C4 烹饪长程任务终版 Benchmark 验收与 `rc4` 修复闭环报告（2026-11-25）

> **最终统一冻结基准源码**：`source_route_ab_rc4`（`feat/rc4-dev @ b689cf70de91d73c26f3d724e90c68cdde883300`，基于 `feat/rc3-dev @ 2323502`，仅含纯 Benchmark 执行器与任务定义修复，**0 改动 harness 与 `skills/`**，全量 185 文件逐文件单测 0 新增失败）。  
> **最终定版任务输入（本地与工作站双目录 SHA256 逐字节一致）**：
> - **C1** `three_appliance_brunch_r9`（三电器早午餐并行，`spec_sha256 = b6e40b3c7c67...`）
> - **C2** `thaw_then_bake_two_rooms_r5`（跨房两段解冻烘焙，`spec_sha256 = 54528a5eb4e3...`，`initial_scene.json` 与 `r4` 逐字节一致）
> - **C3** `dinner_burn_windows_errand_r7`（限时防烤焦双窗晚宴，`spec_sha256 = 740b0f4c4b7d...`）
> - **C4** `after_dinner_three_zone_reset_r6`（餐后三区复位，`spec_sha256 = 06949d6cc34f...`）
>
> **交付结论**：C1–C4 全部 4 道烹饪长程任务已完成 **门禁 4 双参考顺序（8/8 零失败）+ 物理反例（12/12 + `C2 r5` 3/3 精准判负）+ 门禁 5 跨 4 模型 28 局实景排雷（含 `rc4` 复核 4/4 = 100% 全通）**，四题均已在纯基准下由双旗舰模型（`gemini-4-neon/medium` 与 `gpt-6.1-sol/medium`）逐题实测跑通，无剩余 P0/P1 基准缺陷。

---

## 一、`rc3` 首轮全量验收总览（夜间 Gate 4 + Gate 5 排雷）

1. **门禁 4 全闭环（双参考顺序 8/8 零失败 + 物理反例 12/12 精准判负）**：
   - 在冻结源码 `source_route_ab_rc3`（`2323502`）上，C1–C4 全部 **8/8 条参考流程（最优 + 习惯）均为 0 失败、0 重试、0 回滚**，最优相对习惯节省机器人时间 **24.3%–46.9%**；
   - 全部 **12/12 个物理反例（每题 3 个）在 `source_route_ab_rc3` 上 100% 按预期判负**（`task_success = False` 且精准命中目标失分条款）。
2. **门禁 5 夜间三档模型 16 局实景排雷（固定版 `agy 1.5.11`，0 报错）**：
   - **旗舰强模型 `gemini-4-neon/medium`**：**`8/8 (100.0%)` 全通过**（`4/4` 含防早退 + `4/4` 纯操作说明），证实 4 道题全部 RGB 可做、无隐蔽物理/场景死锁；
   - **中档模型 `gemini-3.8-flash/medium`（纯操作说明）**：**`1/4 (25.0%)`**（仅 C3 通过，且机器人时间 `1565.4 s` 比旗舰 `904.2 s` 慢 `+661.2 s`）；
   - **弱模型 `gemini-3.8-flash/low`（纯操作说明）**：**`0/4 (0.0%)`**（平均目标满足率 `0.438`）。
   - **关键留疑项逐一核清**：
     - **C4 `r6` 缩小后的茶几碗 1**：旗舰模型 `2/2` 全通（整局仅 2–3 次失误），实景确认完全可抓、无死锁；
     - **C3 `r7` 904.2 s 优于参考最优（1161.6 s）核查**：逐条核查 `local_manipulation.jsonl` 与 `events.jsonl`，确认系模型真实做到烤箱/微波炉/灶台三电器同时并行 + 双托盘合批（总等待仅 `32 s`），**零物理捷径/作弊**；
     - **中弱模型失败归因**：抽查 `final_state.json` 证实均系真实规划/遗忘/装错电器失误（如 C1 漏送盘子 2 与贝果 2、C4 将餐巾放进洗碗机、C2 超步数预算），**0 处基准假阴性**；
     - **轻微文案改进项**：`C2 r4` 中模型多次把烤箱旁黑色高储藏柜（`bottom_cabinet_fancyy_0`）误作冰箱空抓（未像 `T1` 那样点明白色冰箱位置），已在第六节 `C2 r5` 中修复。

---

## 二、门禁 4：双参考顺序与 12/12 物理反例明细（统一冻结版 `source_route_ab_rc3`）

### 2.1 双参考顺序效率差（0 失败 / 0 回滚）

| 任务 | 习惯顺序（步数 / 机器人时间 / 路径） | 最优顺序（步数 / 机器人时间 / 路径） | 机器人时间节省 | 路径变化 |
|---|---|---|---:|---:|
| **C1 `r9`** | `habit_sequential` · 83 步 · 1372.1 s · 98.9 m | `optimal_longest_first` · 74 步 · 809.0 s · 111.8 m | **-563.1 s (-41.0%)** | +12.9 m (+13.1%) |
| **C2 `r4`** | `habit_sequential` · 131 步 · 2532.6 s · 275.6 m | `optimal_johnson_tray` · 98 步 · 1351.8 s · 208.6 m | **-1180.8 s (-46.6%)** | -67.0 m (-24.3%) |
| **C3 `r7`** | `habit_sequential` · 132 步 · 1534.1 s · 184.6 m | `optimal_errand_in_oven_window` · 110 步 · 1161.6 s · 164.1 m | **-372.5 s (-24.3%)** | -20.5 m (-11.1%) |
| **C4 `r6`** | `habit_sequential` · 66 步 · 1695.4 s · 241.5 m | `optimal_tray_late_pie` · 70 步 · 900.4 s · 86.7 m | **-795.0 s (-46.9%)** | -154.8 m (-64.1%) |

### 2.2 12/12 物理反例全败验证（`source_route_ab_rc3`）

| 任务 | 反例名 | 反例校验状态 | `task_success` | 命中的失败条款 |
|---|---|---|---|---|
| C1 `r9` | `omit_plate_delivery` | `passed` | `False` | `plates_served` |
| C1 `r9` | `omit_cold_delivery` | `passed` | `False` | `tableware_served` |
| C1 `r9` | `omit_stove_off` | `passed` | `False` | `appliances_safe` |
| C2 `r4` | `omit_fish_serve` | `passed` | `False` | `fish_served` |
| C2 `r4` | `omit_living_tableware` | `passed` | `False` | `lasagna_served` |
| C2 `r4` | `omit_lasagna_serve` | `passed` | `False` | `lasagna_served` |
| C3 `r7` | `omit_stove_watch` | `passed` | `False` | `dinner_served`, `appliances_safe`（牛排烤焦） |
| C3 `r7` | `omit_wine_errand` | `passed` | `False` | `wine_served` |
| C3 `r7` | `omit_oven_close` | `passed` | `False` | `appliances_safe` |
| C4 `r6` | `omit_pie_unload` | `passed` | `False` | `pie_served`, `appliances_safe`（派烤焦未上桌） |
| C4 `r6` | `omit_napkin_wash` | `passed` | `False` | `napkins_washed`, `appliances_safe` |
| C4 `r6` | `omit_oven_close` | `passed` | `False` | `appliances_safe` |

---

## 三、门禁 5：三档模型夜间实景基线六项指标（通道：固定版 `agy 1.5.11`，纯基准 `source_route_ab_rc3`）

### 3.1 旗舰强模型 `gemini-4-neon / medium`（`8/8 = 100%`）

| 回合 | 配置 | 成功 / `gf` | 步数（失败步） | 机器人时间（走/操/等） | 墙钟时间 | 认证路径 | Token（输入 / 缓存 / 输出） |
|---|---|---:|---:|---:|---:|---:|---:|
| `rc3_neon_med_C1_auto_r1` | 含防早退 | **True / 1.00** | 55 (**0**) | 868.2 s (388/480/0) | 1668.0 s | 123.2 m | 10.60M / 10.26M / 21.1k |
| `rc3_neon_med_C1_skills_r1` | 纯操作说明 | **True / 1.00** | 55 (**1**) | 827.1 s (377/450/0) | 1385.3 s | 124.9 m | 10.28M / 9.94M / 19.3k |
| `rc3_neon_med_C2_auto_r1` | 含防早退 | **True / 1.00** | 122 (10) | 1627.5 s (673/710/244) | 3392.8 s | 235.7 m | 28.24M / 27.44M / 47.2k |
| `rc3_neon_med_C2_skills_r1` | 纯操作说明 | **True / 1.00** | 128 (16) | 1763.1 s (631/720/412) | 3963.1 s | 208.8 m | 30.40M / 28.92M / 95.2k |
| `rc3_neon_med_C3_auto_r1` | 含防早退 | **True / 1.00** | 85 (**1**) | 981.3 s (459/500/22) | 2370.2 s | 150.0 m | 22.86M / 22.33M / 26.9k |
| `rc3_neon_med_C3_skills_r1` | 纯操作说明 | **True / 1.00** | 71 (**1**) | **904.2 s** (372/500/32) | 2246.1 s | 121.0 m | 16.68M / 15.71M / 24.6k |
| `rc3_neon_med_C4_auto_r1` | 含防早退 | **True / 1.00** | 68 (**3**) | 952.9 s (263/510/180) | 2038.2 s | 92.5 m | 15.57M / 15.15M / 20.8k |
| `rc3_neon_med_C4_skills_r1` | 纯操作说明 | **True / 1.00** | 70 (**2**) | 1113.6 s (364/440/310) | 2941.5 s | 124.6 m | 16.47M / 16.03M / 25.5k |

### 3.2 中档模型 `gemini-3.8-flash / medium`（纯操作说明，`1/4 = 25%`）

| 回合 | 成功 / `gf` | 步数（失败步） | 机器人时间 | 墙钟时间 | 认证路径 | Token（输入 / 缓存 / 输出） | 失败归因 |
|---|---:|---:|---:|---:|---:|---:|---|
| `rc3_flashmed_C1_skills_r1` | False / 0.75 | 89 (13) | 971.7 s | 2601.7 s | 99.7 m | 11.87M / 11.27M / 61.8k | 漏送盘子 2 与微波炉内贝果 2 |
| `rc3_flashmed_C2_skills_r1` | False / 0.20 | 174 (16) | 1239.6 s | 3978.4 s | 246.5 m | 23.30M / 22.10M / 159.0k | 耗尽步数预算（鱼煲未解冻烘焙） |
| `rc3_flashmed_C3_skills_r1` | **True / 1.00** | 167 (11) | 1565.4 s | 4225.9 s | 215.3 m | 23.49M / 22.36M / 122.9k | 通过（比 `neon` 慢 +661 s） |
| `rc3_flashmed_C4_skills_r1` | False / 0.20 | 177 (12) | 1509.7 s | 4421.2 s | 294.9 m | 24.24M / 23.01M / 174.2k | 将餐巾 1 错放洗碗机、碗 2 掉走廊、耗尽步数 |

### 3.3 弱模型 `gemini-3.8-flash / low`（纯操作说明，`0/4 = 0%`）

| 回合 | 成功 / `gf` | 步数（失败步） | 机器人时间 | 墙钟时间 | 认证路径 | Token（输入 / 缓存 / 输出） |
|---|---:|---:|---:|---:|---:|---:|
| `rc3_flashlow_C1_skills_r1` | False / 0.50 | 172 (45) | 1317.8 s | 3025.3 s | 178.9 m | 23.78M / 22.55M / 18.7k |
| `rc3_flashlow_C2_skills_r1` | False / 0.40 | 179 (23) | 2538.4 s | 3758.3 s | 604.7 m | 24.74M / 23.65M / 22.0k |
| `rc3_flashlow_C3_skills_r1` | False / 0.25 | 128 (18) | 1439.6 s | 2651.8 s | 383.1 m | 17.64M / 16.79M / 14.5k |
| `rc3_flashlow_C4_skills_r1` | False / 0.60 | 179 (33) | 1501.9 s | 3621.8 s | 277.3 m | 25.02M / 23.93M / 20.8k |

---

## 四、白天首轮：`gpt-6.1-sol / medium`（`ccswitch`）8 局实景排雷与 P0 缺陷定位（`source_route_ab_rc3`）

| 回合 | 配置 | 成功 / `gf` | 步数（失败步） | 机器人时间（走/操/等） | 墙钟时间 | 认证路径 | Token（输入 / 缓存 / 输出） | 归因判定 |
|---|---|---:|---:|---:|---:|---:|---:|---|
| `rc3_sol_med_C1_skills_r1` | 纯操作说明 | **True / 1.00** | 69 (5) | 934.0 s (344/590/0) | 2118.2 s | 109.6 m | 5.10M / 4.95M / 5.0k | 通过（零等待并行） |
| `rc3_sol_med_C1_auto_r1` | 含防早退 | **True / 1.00** | 70 (8) | **863.7 s** (314/550/0) | 5643.9 s | 96.7 m | 5.43M / 5.26M / 5.1k | 通过（零等待并行） |
| `rc3_sol_med_C2_skills_r1` | 纯操作说明 | False / 0.80 | 93 (11) | 1369.1 s (588/480/301) | 2813.2 s | 193.0 m | 9.27M / 9.04M / 7.1k | **实锤 P0 基准物理 Bug**（见下） |
| `rc3_sol_med_C2_auto_r1` | 含防早退 | **True / 1.00** | 106 (6) | 1569.4 s (937/570/62) | 3527.0 s | 340.1 m | 12.06M / 11.72M / 7.8k | 通过 |
| `rc3_sol_med_C3_skills_r1` | 纯操作说明 | False / 0.50 | 28 (5) | 268.0 s (138/130/0) | 858.3 s | 39.8 m | 1.05M / 0.98M / 4.1k | 纯早退（无防早退） |
| `rc3_sol_med_C3_auto_r1` | 含防早退 | False / 0.50 | 39 (16) | 402.7 s (83/140/180) | 2036.2 s | 23.3 m | 1.86M / 1.78M / 4.5k | 真实模型操作失误（手持菜开已关烤箱/往墙侧放物） |
| `rc3_sol_med_C4_skills_r1` | 纯操作说明 | False / 0.40 | 32 (13) | 238.9 s (109/130/0) | 1079.9 s | 35.4 m | 1.31M / 1.21M / 4.1k | 纯早退（无防早退） |
| `rc3_sol_med_C4_auto_r1` | 含防早退 | **True / 1.00** | 113 (**2**) | 1641.5 s (812/650/180) | 4085.6 s | 286.1 m | 13.45M / 12.98M / 8.7k | **通过（仅 2 失败步，确认 C4 r6 碗 1 与洗碗机稳健）** |

### 抓出的唯一 P0 基准物理 Bug（`C2 r4` 烤箱撞飞）
- **现象**（`rc3_sol_med_C2_skills_r1`）：`c2_lasagna` 已稳放在烤箱上层烤架；随后往烤箱下层 `place_inside(c2_fish, oven)` 时撞飞了上层 `c2_lasagna`，使其侧翻落在打开的烤箱门板上，但 `place_inside` 未回滚仍报 `ok`；随后 `close(oven)` 将门板上的 `c2_lasagna` 压穿烤箱底板夹进柜底（`z = 0.75–0.97 m`，彻底隐身不可抓，但评估器仍判 `lasagna_baked = True`）。
- **根因**（`src/manipulation_agent/executors/placement_stable.py`）：`_standing_neighbours()` 过滤掉了 `_upright_meaningful(obj) == False` 的扁平物品（扁平烤盘 `c2_lasagna` 被直接跳过），且校验未检查容器内既有物体是否移出腔体或发生大幅位移，导致撞飞扁平既有物时静默放行。

---

## 五、基准修复与终版闭环验收（`source_route_ab_rc4` + `C2 r5`，2026-11-25 12:14–20:33）

针对上述 P0 物理事务缺陷及 `C2 r4` 冰箱外观描述歧义，在分支 `feat/rc4-dev`（提交 `b689cf70`，基于 `2323502`）完成了纯 Benchmark 侧修复（仅改 6 个基准文件，**0 改动 harness 与 `skills/`**），通过全量 185 文件逐文件单测（`1570 passed + 513 subtests`，`0` 新增失败），双端三份一致锁定只读冻结源码 `source_route_ab_rc4`（`file_count=729`，`tree_sha256=92458e38...`），并同步新输入版本 `inputs/thaw_then_bake_two_rooms_r5`。

### 5.1 修改清单（改之前 / 改之后 / 为什么）

1. **通用执行器：容器全量既有物体保护与紧凑净空回退**（`executors/inside_sampling.py`、`executors/placement_stable.py`、`executors/placement.py`、`tests/test_rc4_inside_resident_guard.py`）
   - **改之前**：`place_inside` 仅对高瘦物体（`_upright_meaningful == True`）做倾角校验，跳过扁平烤盘且不检查容器内既有物位移/出腔；`inside_sampling.py` 仅使用 `+2.53 cm` 净空。
   - **改之后**：在 `placement_stable.py` 新增通用 `resident_snapshot` / `check_residents`（校验 `still_inside`、位移 `<= 0.12 m`、倾角变化 `<= 45°`，不按高矮过滤）/ `restore_residents` / `restored_resident_aabbs`，接入 `placement.py` 的 `_checked_place_inside`（受 `MAS_PLACE_STABLE_CHECK` 控制，默认开启）；在 `inside_sampling.py` 中，仅当所有标准 `+2.53 cm` 候选位均有重叠（`overlap > 0`）时，启用 `0.003 m` 紧凑净空回退。
   - **为什么**：从通用物理事务层根除“容器放第二件物体撞飞既有扁平物却静默报成功”的 P0 基准缺陷，零任务特化。
2. **任务 `C2 r5` 中性冰箱描述**（`task_designs/cooking_v1/thaw_then_bake_two_rooms/{make_recipe.py,recipe.json}`、`inputs/thaw_then_bake_two_rooms_r5`）
   - **改之前**：`C2 r4` 仅写 `"refrigerator"`，导致模型多次误开烤箱旁黑色储物高柜 `bottom_cabinet_fancyy_0`。
   - **改之后**：在 `C2 r5` 指令中补入与 `T1` 相同的中性外观描述 `"(the tall white appliance between the sink counter and the black built-in oven)"`，场景文件 `initial_scene.json` 与 `r4` 完全一致。
   - **为什么**：消除与规划决策无关的外观认错噪音，保持物理结构与效率差 100% 不变。

### 5.2 门禁 4 物理仿真全量回归（`source_route_ab_rc4`，PRO 6000，`8/8` 零失败动作）

| 验证项 | 任务版本与模式 | 结果 / 评估 | 动作数（失败步） | 机器人时间 | `residents_kept` 校验 |
|---|---|---|---:|---:|---|
| `c1r9_optimal` | `C1 r9` 最优顺序 `optimal_longest_first` | **PASS** / `succ=True` | 74 (0) | 816.5 s | 全 `True` |
| `c2r5_optimal_warm` | `C2 r5` 最优顺序 `optimal_johnson_tray` | **PASS** / `succ=True` | 98 (0) | **1351.8 s** | `oven_ffitak_0` = **`True`** |
| `c2r5_habit_warm` | `C2 r5` 习惯顺序 `habit_sequential` | **PASS** / `succ=True` | 131 (0) | **2532.6 s** | 最优省 **`-46.6%`** |
| `c3r7_optimal_warm` | `C3 r7` 最优顺序 `optimal_errand_in_oven_window` | **PASS** / `succ=True` | 110 (0) | 1161.6 s | 全 `True` |
| `c4r6_optimal` | `C4 r6` 最优顺序 `optimal_tray_late_pie` | **PASS** / `succ=True` | 71 (0) | 902.8 s | 全 `True`（4 次） |
| `c2r5_neg_fish` | `C2 r5` 反例 `omit_fish_serve` | **PASS** / `succ=False` | 94 (0) | 1261.9 s | 仅 `fish_served=False` |
| `c2r5_neg_living` | `C2 r5` 反例 `omit_living_tableware` | **PASS** / `succ=False` | 96 (0) | 1355.9 s | 仅 `lasagna_served=False` |
| `c2r5_neg_lasagna` | `C2 r5` 反例 `omit_lasagna_serve` | **PASS** / `succ=False` | 95 (0) | 1324.6 s | 仅 `lasagna_served=False` |

### 5.3 门禁 5 实景复核：`gpt-6.1-sol / medium`（`ccswitch` 通道，纯基准 `source_route_ab_rc4`，**4/4 = 100% 全通**）

| 回合 | 任务与配置 | 成功 / `gf` | 步数（失败步） | 机器人时间（走/操/等） | 墙钟时间 | 认证路径 | Token（输入 / 缓存 / 输出） | 逐帧与事务审计结论 |
|---|---|---:|---:|---:|---:|---:|---:|---|
| `rc4_sol_med_C2r5_skills_r1b` | `C2 r5` 纯操作说明 | **True / 1.00** | 111 (6) | 1606.1 s (848/620/138) | 17584.6 s | 295.6 m | 13.22M / 12.96M / 8.9k | 反向先鱼后千层面，`residents_kept=True`，0 撞飞、0 回滚、0 误开黑柜 |
| `rc4_sol_med_C2r5_skills_r2` | `C2 r5` 纯操作说明 | **True / 1.00** | 107 (7) | 1467.9 s (811/620/37) | 4563.6 s | 293.8 m | 12.23M / 11.93M / 8.1k | 正向先千层面后鱼，`residents_kept=True`，0 撞飞、0 回滚、0 误开黑柜 |
| `rc4_sol_med_C2r5_auto_r1` | `C2 r5` 含防早退 | **True / 1.00** | 93 (**4**) | **1447.1 s** (705/520/222) | 3863.0 s | 261.9 m | 9.43M / 9.11M / 6.9k | 正向先千层面后鱼，`residents_kept=True`，0 撞飞、0 回滚、0 误开黑柜，较 `rc3 auto` 快 `-122 s` |
| `rc4_sol_med_C3r7_auto_r1` | `C3 r7` 含防早退 | **True / 1.00** | 106 (8) | 1400.4 s (601/770/29) | 16877.4 s | 205.8 m | 12.17M / 11.90M / 7.8k | 三电器并行与防糊窗控火全通，0 事务回滚，全终端条款 `True` |

---

## 六、C1–C4 最终交付定版清单

| 编号 | 最终任务输入目录 | 对应冻结源码 | 门禁 4 双顺序效率差 | 双旗舰模型实景跑通状态 |
|---|---|---|---|---|
| **C1** | `inputs/three_appliance_brunch_r9` | `source_route_ab_rc4` (`b689cf70`) | `-41.0%`（`816.5 s` vs 习惯） | `neon` 2/2、`sol` 2/2 + `rc4` 物理回归 0 失败 |
| **C2** | `inputs/thaw_then_bake_two_rooms_r5` | `source_route_ab_rc4` (`b689cf70`) | `-46.6%`（`1351.8 s` vs `2532.6 s`） | `sol` **`3/3`（`rc4`）** + `neon` 2/2（`r4`） |
| **C3** | `inputs/dinner_burn_windows_errand_r7` | `source_route_ab_rc4` (`b689cf70`) | `-24.3%`（`1161.6 s` vs 习惯） | `sol` **`1/1`（`rc4`）** + `neon` 2/2 + `flash/med` 1/1 |
| **C4** | `inputs/after_dinner_three_zone_reset_r6` | `source_route_ab_rc4` (`b689cf70`) | `-46.9%`（`902.8 s` vs 习惯） | `neon` 2/2、`sol` 1/1 `auto`（仅 2 失败步）+ `rc4` 物理回归 0 失败 |
