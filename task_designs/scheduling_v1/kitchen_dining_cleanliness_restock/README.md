# Task 5: 餐厨洁净度单调清洁与储物闭柜盲盒盘点补齐 (`kitchen_dining_cleanliness_restock`)

## 1. 任务概述与设计动机

`kitchen_dining_cleanliness_restock`（餐厨洁净度单调清洁与储物闭柜盲盒盘点补齐）是长程具身移动操作 Benchmark 中的拓扑结构规划任务（Task 5）。

### 核心规划特性与复杂度
本任务无任何外部设备耗时等待（设备定时等待为 0.0s），纯粹由**拓扑约束、单向单调性剪枝、信息盲盒与载具容量限制**构成深度规划问题：
1. **单工具洁净度单调约束与去污重置（Tool Cleanliness Monotonicity & Decontamination Reset）**：
   - 抹布（`rag`）的污染度标量状态 $D \in \{0, 1, 2, 3\}$，初始 $D=0$。
   - 场景包含 3 个洁净度等级的表面：
     - **Level 1（高洁净区）**：`dining_table`（餐桌）、`prep_counter`（备餐台）。
     - **Level 2（标准洁净区）**：`cabinet_shelf`（储物柜隔板）、`bar_counter`（吧台）。
     - **Level 3（重油污区）**：`stove_top`（灶台台面）、`waste_surround`（垃圾桶周边）。
   - **单调性转移律**：抹布擦拭 surface 等级 $L$ 时，要求抹布当前污染度 $D \le L$；擦拭后抹布污染度单调提升 $D \leftarrow \max(D, L)$。若在 $D > L$ 时擦拭，则立即触发 `cross_contamination_violation = True`（交叉污染违规）。
   - **去污重置**：抹布放入水槽（`sink`）浸泡清洗（`soak`）可且仅可在此时将污染度完全重置为 $0$（$D \leftarrow 0$）。
2. **闭柜盲盒与盘点驱动的补货决策（Closed Blind-Box Inventory & Sourcing）**：
   - 目标储物柜（`target_cabinet`）初始处于关闭状态（`closed`），内部存放未知品类与数量的饮品/零食（盲盒状态）。
   - 最终目标柜内要求存放：果汁 2 瓶（`juice_1`, `juice_2`）、茶饮 2 瓶（`tea_1`, `tea_2`）、点心 2 盒（`snack_1`, `snack_2`），共 6 件。
   - 初始柜内仅有 `juice_1`, `tea_1`, `tea_2`，且柜板积尘（`cabinet_shelf` 处于 dirty 状态）。
   - **两重强前置依赖**：
     - 柜内状态只有在打开柜门（`open_articulated`）后方可盘点确认为缺少 1 瓶果汁与 2 盒零食；
     - 柜板清洁守卫（`cabinet_clean_before_stock`）：柜隔板被抹布擦拭干净前，不得向柜内放入任何补货物品。
3. **载具容量与巡回合并优化（Batch Carrier Tour vs. Habit Sequential）**：
   - 补货源位于备餐间架子（`pantry_shelf`），机器人单手单次只能持拿 1 件物品。
   - 场景中提供托盘（`tray`），容量 $\ge 3$。
   - 习惯性顺序执行（`habit_sequential`）多次单件往返、未按洁净度单调排序导致抹布多次往返水槽清洗；最优巡回（`optimal_carrier_tour`）将高洁净度表面先行擦拭、开柜盘点顺带清洁柜板、擦拭油污区后统一清洗抹布并归位，利用托盘单次批量将 3 件物品自架子转运至柜内并关门，节省 $\ge 25\%$ 的机器人时间与移动路程。

---

## 2. 场景布局与实体清单

- **基础场景模板**：`preparing_lunch_box` instance 301, seed 0 (`house_single_floor`)。
- **设备耗时等待**：0.0s（无烤箱/微波炉等长定时等待，全时域物理移动与操作调度）。
- **关键实体与角色映射**：
  | 逻辑角色 | 实体名称 / 资产类别 | 洁净度等级 / 特性 | 初始位置 / 状态 |
  |---|---|---|---|
  | `sink` | `sink/sink_0` | 水源，抹布重置区 | 厨房水槽 |
  | `rag` | `rag/oocmed` | 清洁工具，污染度 $D \in \{0..3\}$ | 餐桌台面，clean |
  | `tray` | `tray/coqeme` | 运输载具（容量 $\ge 3$） | 吧台台面 |
  | `dining_table` | `dining_table` (surface) | Level 1 (High) | 餐厅 |
  | `prep_counter` | `prep_counter` (surface) | Level 1 (High) | 厨房备餐台 |
  | `target_cabinet` | `cdkr_cabinet` (articulated) | 目标闭柜盲盒 | 餐厅角 niche，初始 closed |
  | `cabinet_shelf` | `cdkr_shelf` (surface inside) | Level 2 (Standard) | 目标柜内部，初始 dirty |
  | `bar_counter` | `bar_counter` (surface) | Level 2 (Standard) | 吧台台面 |
  | `stove_top` | `stove_top` (surface) | Level 3 (Heavy Stain) | 厨房灶台 |
  | `waste_surround`| `waste_surround` (surface) | Level 3 (Heavy Stain) | 垃圾桶周围区域 |
  | `pantry_shelf` | `shelf/sapyzl` | 补货源存放架 | 备餐间 |
  | `juice_1` | `bottle_of_apple_juice/xvrbdy` | 柜内初始饮品 | 目标柜内部隔板 |
  | `juice_2` | `bottle_of_apple_juice/xvrbdy` | 待补饮品 | 备餐间架子 |
  | `tea_1` | `bottle_of_tea/iladfg` | 柜内初始茶饮 | 目标柜内部隔板 |
  | `tea_2` | `bottle_of_tea/yatmrs` | 柜内初始茶饮 | 目标柜内部隔板 |
  | `snack_1` | `box_of_crackers/cmdigf` | 待补点心 | 备餐间架子 |
  | `snack_2` | `box_of_crackers/cmdigf` | 待补点心 | 备餐间架子 |

---

## 3. 谓词体系与评估守卫 (Evaluator Guards)

### 3.1 谓词定义
1. `wiped(surface)`：对应表面是否已被清洁布擦拭。
2. `cross_contamination_violation`：抹布当前污染度 $D > \text{level}(surface)$ 时执行擦拭，触发交叉污染违规。
3. `closed(target_cabinet)`：柜门处于完全关闭锁定状态。
4. `inside(item, target_cabinet)`：物品位于目标柜内。
5. `ontop(tray, bar_counter)`：托盘整洁停放在吧台。
6. `inside(rag, sink)`：抹布清洁后在水槽彻底冲洗干净重置并归位。
7. `hands_free`：双手均为空闲状态。

### 3.2 守卫规则 (Guards)
- **`no_cross_contamination`**：全程守卫。若任意时刻 `cross_contamination_violation == True`，评估器立即判负。
- **`cabinet_clean_before_stock`**：先决条件守卫。在 `cabinet_shelf` 变为 `clean` 之前，任何新物品放入 `target_cabinet` 均使该守卫失效并终止回合。

### 3.3 终态目标 (Terminal Goals)
1. 6 个指定表面擦拭干净：`dining_table`, `prep_counter`, `cabinet_shelf`, `bar_counter`, `stove_top`, `waste_surround` 均为 clean。
2. 目标柜内完成补齐：`juice_1`, `juice_2`, `tea_1`, `tea_2`, `snack_1`, `snack_2` 共 6 件均位于 `target_cabinet` 内部。
3. 柜门密闭：`target_cabinet` 为 closed。
4. 抹布已去污冲洗并停泊：`inside(rag, sink)`。
5. 托盘停放归位：`ontop(tray, bar_counter)`。
6. 机器人双手空闲：`hands_free`。

---

## 4. 参考方案对比与效率差距 (Theoretical Efficiency Gap)

### 4.1 最优载具巡回方案 (`optimal_carrier_tour`)
1. **L1 清洁**：拿取抹布，依次擦拭 `dining_table` 与 `prep_counter`（$D: 0 \to 1$）。
2. **开柜与 L2 清洁**：移动至目标柜，打开柜门确认存货，擦拭 `cabinet_shelf`，顺手擦拭 `bar_counter`（$D: 1 \to 2$）。
3. **L3 清洁与去污**：移动至厨房，擦拭 `stove_top` 与 `waste_surround`（$D: 2 \to 3$）；在 `sink` 浸泡去污重置抹布并停泊（$D \to 0$）。
4. **托盘批量补货**：持拿托盘至备餐间架子，批量装载 `juice_2`, `snack_1`, `snack_2`；转运至目标柜一次性卸载装入；将托盘归位至吧台。
5. **关门与收尾**：关闭目标柜门，双手放空。
- **预期指标**：100% 成功率，0.0s 等待，机器人动作次数约 42 步，路程与时间达到理论最优下界。

### 4.2 习惯性次优顺序 (`habit_sequential`)
1. **逆序/无序擦拭**：先擦油污灶台（$D: 0 \to 3$），随后不得不往返水槽清洗；再擦餐桌、吧台等，导致多轮反复前往水槽去污。
2. **盲盒无托盘单件往返**：打开柜门后，单件往返备餐间 3 次搬运补货。
3. **效率差距分析**：
   - 相比最优巡回方案，习惯性方案增加至少 3 次全房间长程往返与 2 次抹布去污往返。
   - 理论效率提升：机器人时间减少 $\ge 25\%$，总路程减少 $\ge 25\%$。

---

## 5. 物理负样本测试用例 (Physical Negative Tests)

在 PRO 6000 上针对独立评估器运行 4 组精确物理消融负样本，严格保证无误判：
1. **`omit_sink_soak_contamination`**：
   - 流程中故意跳过水槽清洗环节，在擦拭重污区（L3）后直接擦拭餐桌（L1）。
   - 预期结果：触发 `no_cross_contamination` 守卫违规，评估器判定失败（0% 通过率）。
2. **`omit_cabinet_wiping`**：
   - 跳过擦拭柜板步骤，直接向未擦拭的脏柜板放入补货物品。
   - 预期结果：触发 `cabinet_clean_before_stock` 守卫违规，评估器判定失败（0% 通过率）。
3. **`omit_snack_restock`**：
   - 完成所有清洁，但因盘点疏漏未补齐点心（缺少 `snack_1` 与 `snack_2`）。
   - 预期结果：未达成 6 件物品在柜内的终态条件，评估器判定失败（0% 通过率）。
4. **`omit_door_closing`**：
   - 完成所有清洁和补货，但未关闭柜门直接结束任务。
   - 预期结果：未达成 `closed(target_cabinet)` 条件，评估器判定失败（0% 通过率）。
