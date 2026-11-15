# Task 6: Tabletop Buffer Nonmonotonic Swap (`tabletop_buffer_nonmonotonic_swap`)

## 任务目标与设计哲学
本任务为长程具身移动操作规划基准中的核心非等待规划任务（Milestone 4）。
在**无设备计时等待**（设备等待 0.0s）的前提下，构建高规划复杂度的**空间路由 + 有限中转暂存 (K=2) + 双向移动载具置换 (C=2) + 腾空擦拭守卫与洁净放物时序约束**。

## 场景实体与工位布局
- **底座场景**：`spraying_for_bugs` instance 301, seed 0 (`house_double_floor_lower`)
- **工位 1**：厨房岛台 `kitchen_island`（`bar_byvbuc_0`，pos: `[6.75, -0.71, 0.40]`），初始覆盖灰尘微粒，摆放物品 $\mathcal{B} = \{B_1: \text{ketchup}, B_2: \text{mug}\}$。
- **工位 2**：客厅餐桌 `dining_table`（`tbns_dining_table`，pos: `[3.50, 5.30, 0.63]`），初始覆盖灰尘微粒，摆放物品 $\mathcal{A} = \{A_1: \text{bowl}, A_2: \text{teapot}\}$。
- **中转暂存台**：`staging_table`（`tbns_staging_table`，pos: `[5.80, 0.80, 0.45]`），初始腾空洁净，容量上限 $K=2$。
- **移动载具**：`tray`（`tbns_tray`，初始位于厨房岛台），容量 $C=2$。
- **洁净工具**：`rag`（`tbns_rag`，初始位于水槽工具架 `sink`，pos: `[8.11, 0.23, 0.86]`）。

## 核心时序与状态守卫契约（`transition_guards`）
1. `wipe_island_only_when_empty` / `wipe_dining_only_when_empty`：
   工位必须在完全腾空无物状态下（`all items OnTop == False`），方允许执行抹布擦拭将 `Covered(dust)` 转为 `False`。若工位有物品时擦拭，触发不可逆违规。
2. `place_island_only_when_clean_*` / `place_dining_only_when_clean_*`：
   工位必须已彻底除尘（`Covered(dust) == False`），方允许放置新物品。若向脏工位放置物品，触发交叉污染不可逆违规。

## 零等待规划效率增益（理论值）
- **习惯性顺序（`habit_sequential`）**：单件反应式往返，18 次单件搬运与工具拿放，总路程 $146.5\text{ m}$，机器人时间 $527.0\text{ s}$，等待 $0.0\text{ s}$。
- **最优前瞻置换（`optimal_carrier_tour`）**：带工具直奔客厅餐桌腾空擦净，载具单趟跨区到厨房，卸入暂存台腾出载具，腾空岛台即刻擦净，暂存物品移上岛台，原岛台物品单趟送往餐桌摆放，工具归位。总路程 $49.0\text{ m}$，机器人时间 $360.0\text{ s}$，等待 $0.0\text{ s}$。
- **收益比**：
  - 路程缩短：$(146.5 - 49.0) / 146.5 = \mathbf{66.6\%} \ge 25\%$
  - 机器人时间缩短：$(527.0 - 360.0) / 527.0 = \mathbf{31.7\%} \ge 25\%$
