# 任务 T7：闭柜未知存货精确配平与压盖工具箱多跳解锁（`blind_cabinet_exact_restock_unlock`）

## 一、任务定位与核心规划难点（`0 s` 等待，覆盖 M1 + M5 + M6）

本任务严格依据《多模式规划难任务构造与调研（2026-11-15）》第五节“候选 T7”与第三节“五条防坑铁律”构建，是一道**完全不含任何家电计时等待（设备等待时间 `0.0 s`）**的跨区域长程规划任务，同时耦合三类通用物理规划结构：

1. **M5：闭门柜未知存货条件盘点与盈余退货代价（Contingent Blind-Cabinet Exact Balancing with Surplus Penalty）**
   - **北端客房（Guest Bedroom，`y ≈ 22.4..25.4`）**设有两座初始紧闭的高储物柜：
     - 东墙闭门柜 `cabinet_east`（初始内含 `1` 瓶水 + `1` 盒饼干，缺 `1` 瓶苹果汁）；
     - 西墙闭门柜 `cabinet_west`（初始内含 `1` 瓶苹果汁，缺 `1` 瓶水 + `1` 盒饼干）。
   - **南端杂物间（Utility Room，`y ≈ -0.3..1.8`，跨区单程原生 A* 路径长度 `24.0–25.5 m`）**储物台面 `utility_counter` 上放有**盈余存货**（`2` 瓶水 + `2` 瓶苹果汁 + `2` 盒饼干，共 6 件，而北端实际总缺口仅 3 件）及一只木质托盘 `carrier`（`48 × 48 cm`）。
   - **精确配平与盈余守恒终态**：北端每座柜子终态必须**恰好**含 `1` 瓶水、`1` 瓶苹果汁、`1` 盒饼干（严禁多塞冗余物资）并关好柜门，且南端未使用的全部盈余物资（各 `1` 件）必须留在南端杂物间台面上。若不先在北端开柜盘点就盲目去南端多拿物资北上，发现柜内已有存货后**被迫再走一趟 `~50 m` 南北往返将多余物资退回南端台面**。
2. **M6：走廊压盖工具箱多跳解锁链（Multi-Hop Blocked-Container Unlock）**
   - 点亮书房书桌台灯 `study_lamp` 与北端客房床头台灯 `guest_lamp` 所需的两枚灯泡（`bulb1`、`bulb2`）锁在东走廊工作台 `tool_bench` 的闭合金属工具箱 `toolbox` 内。
   - `transition_guards`（`toolbox_open_requires_books_cleared`）规定：只要两本平装书（`book1`、`book2`）仍有任意一本留在走廊工作台或工具箱顶面，严禁打开工具箱盖。必须先完成四跳因果解锁链：
     $$\text{清空工作台平装书} \rightarrow \text{上架至书房门外墙架 } \texttt{hall\_shelf} \rightarrow \text{打开工具箱取出双灯泡并关箱} \rightarrow \text{装配灯泡并点亮双房台灯}$$
3. **M1：顺路载具合并（Carrier Tour Synergy）**
   - 机器人初始位于北走廊（`[19.15, 18.00]`）。最优策略先顺路北上 `5 m` 打开两座客房柜门摸清精确缺口（各缺哪些品类），南下途中将两本书上架解锁工具箱，直达南端杂物间用托盘精确装载 `1 水 + 1 果汁 + 1 饼干`，北返途经走廊工作台时打开已解锁的工具箱取出两枚灯泡（一枚顺手装亮书房台灯、另一枚搭托盘顺路带往北端客房），一趟抵达北端完成两柜精确补齐、关门与客房台灯装配点亮。

## 二、五条合理任务标准核验

1. **做得成**：复用 1cm 级验证通过的单层住宅底座（`clean_up_your_desk` instance 301, seed 0，`house_single_floor`），全流程仅含刚体抓放、铰接门开合、灯泡 `attach` 与台灯 `toggle_on`，无流体/布料/微粒/计时器物理随机性。
2. **零歧义与零网格拆分陷阱**：剔除同款多余床头柜、多余台灯与走廊其余 11 块 `shelf_amyper_*` 隔板，使 `shelf_amyper_2` 成为书房门外唯一墙架；南端 `countertop_ikwqer_0` 为单块连续 `4.47 m` 台面，无连体台面子网格拆分陷阱。
3. **100% 策略中立公开指令**：`public_instruction` 与 `public_context` 仅客观陈述终态条件与工具箱开盖守卫规则，零策略暗示词（经 `tests/test_dual_benchmark_and_t7.py` 正则校验）。
4. **大体积场景快照零入库**：`make_recipe.py`、`recipe.json`、`task.json` 入库 Git；`initial_scene.json` 存放于仓库外 `/mnt/data/home/sunyujie/robot_manipulation/experiments/route_ab_team_20261115/inputs/blind_cabinet_exact_restock_unlock/`（远端同路径镜像）。

## 三、远端 RTX PRO 6000 物理验收数据

- **冻结契约 `task.json` SHA-256**：见 `task_designs/dual_benchmark_suite_20261115.json`（随场景构建自动锁定）。
- **双特权参考流程对比（`0.0 s` 等待）**与 **4 项物理违规负例**（`violate_toolbox_book_guard`、`omit_cabinet_restock`、`omit_guest_lamp_lighting`、`omit_cabinet_doors_closing`）完整实测结果汇总见 [docs/dual_benchmark_suite_20261115.md](../../../docs/dual_benchmark_suite_20261115.md)。

## 四、PRO 6000 实测结果（契约 `task.json` SHA-256 `23f559b2afd1a2a8eb15f373423e59a716219d53236dc83a7f6cdf8456291c31`，场景 SHA-256 `9f7ea072112535353eb75fe9cf31b6f641325fcd06cd662ae6d164a85e400402`）

| 项 | 成功 | 机器人时间 | 路程 | 等待 |
|---|---|---|---|---|
| 最优参考 `optimal_inspect_unlock_carrier_loop` | 是 | 667.4 s | 114.9 m | 0 s |
| 习惯参考 `habit_blind_overfetch_sequential` | 是 | 1146.0 s | 332.7 m | 0 s |
| 最优相对习惯 | — | **省 41.8%** | **省 65.5%** | — |

四个负例均被独立评估器判失败：`violate_toolbox_book_guard`（0.8，触发守卫）、`omit_cabinet_restock`（0.6）、`omit_guest_lamp_lighting`（0.9）、`omit_cabinet_doors_closing`（0.9）。跨区单程 A* 实测约 25 m（要求 ≥15 m）。
