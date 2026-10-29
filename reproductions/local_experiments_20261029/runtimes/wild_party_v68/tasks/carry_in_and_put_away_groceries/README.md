# 第二个长程任务：搬运并收纳杂货

当前包是已实验的**短版可行性切片**。围绕同一次采购、两袋杂货分区入库的扩展任务见 [v2 任务包](v2_design/README.md)；已构建研究场景并完成首轮模型实验，任务失败，详情见该目录说明。

任务使用公开 `carrying_in_groceries` 的 `house_double_floor_lower` 场景与实例 301，另写一份本地 BDDL。人类指令见 [`instruction.txt`](instruction.txt)：将车库汽车后备箱的购物袋带到厨房，把番茄和牛奶放进冰箱，把空袋放在冰箱旁，并关好后备箱和冰箱。与公开 BDDL 相比，新增“购物袋在冰箱旁”的终态关系，防止机器人只搬运两件食品却把购物袋留在车库。

这是一项**自定义原生 BDDL 任务**：目标仍由 OmniGibson/BEHAVIOR 原生谓词在仿真状态中判定，但它的分数不是公开挑战任务成绩。运行时在实验目录中创建 BDDL overlay；公开 BDDL、场景模板与实例文件保持原样。[来源清单](source_manifest.json) 固定了公开 BDDL、两份场景模板和实例状态的 SHA-256。

## 作者侧目标与依赖

[`partial_order.json`](partial_order.json) 记录目标图，不发送给模型。取袋、打开车库通往室内的小门、带袋进入室内是过程里程碑；开门时若手中拿着袋子，须先放下，开门后再取袋。番茄和牛奶可以按任意顺序收纳；关后备箱可与室内工作交错；两件食品进入冰箱后，才将关冰箱计作完成。最终五项证据分别是番茄入冰箱、牛奶入冰箱、购物袋在冰箱旁、后备箱关闭、冰箱关闭。BDDL 的最终状态允许其他合法操作顺序，图中的依赖用于追踪任务叙事。

## 构建与验证

在仓库根目录运行静态检查：

```bash
python3 tasks/carry_in_and_put_away_groceries/verify.py \
  --behavior-root ../behavior_challenge_sunyujie/BEHAVIOR-1K
PYTHONPATH=src python3 scripts/check_atomic_action_coverage.py \
  ../behavior_challenge_sunyujie/BEHAVIOR-1K/bddl3/bddl/activity_definitions \
  --bddl-file tasks/carry_in_and_put_away_groceries/problem0.bddl
```

正式回合使用 `scripts/run_no_demo_trial.py --task carrying_in_groceries --instance 301`，同时传入本包的 `--bddl-source` 和 `instruction.txt` 内容、独立输出目录、长程规划 skill 开关及实验交接指南规定的模型、provider、GPU 和预算。关键任务参数为：

```text
--task carrying_in_groceries --instance 301 \
--instruction "$(cat tasks/carry_in_and_put_away_groceries/instruction.txt)" \
--bddl-source tasks/carry_in_and_put_away_groceries/problem0.bddl \
--long-horizon-planning-skill on \
--cpu-dynamics
```

runner 只在本地 BDDL 与公开定义不同时创建 overlay，并把结果标为 `custom_native_bddl`；`official_task_success` 留空，单列 `custom_task_success`。不使用热饮专属的 `--high-level-harness`。`--cpu-dynamics` 是本场景动作诊断已验证的配置；最初的 GPU 动力学动作探针在导航时发生 PhysX CUDA 错误，CPU 动力学下可执行取袋与开门动作。这不代表 CPU 配置已经通过完整回合。

正式回合之前，还须用真实 RGB/动作探针验证：车库首帧能定位汽车与购物袋；从车内取袋后，袋内物品随搬运保持；可从车库导航到厨房；番茄和牛奶可分别放进冰箱；空袋可放在冰箱旁；两扇门的物理状态与最终谓词一致。每项探针都保存前后图像、动作反馈和独立谓词结果。探针只证明机制可行，不能计作视觉自主成功。

## 当前验证状态（2026-10-19）

静态来源校验和动作谓词覆盖检查通过；真实 OmniGibson 初始化了实例 301 和自定义 BDDL，五个终态谓词的初值为 `[false, false, false, false, true]`。CPU 动力学、四路 RGB 的开发者选点诊断成功从车尾箱抓取纸袋；执行器记录袋内有两件刚体。尝试拿袋开室内门返回 `hand_occupied`；放下纸袋、打开室内门、再次抓袋均成功，再次抓取仍记录两件袋内刚体。诊断随后到达门口附近，但几次朝向室内过道的视觉导航均返回 `navigation_unreachable`，未抵达厨房。结束时自定义任务成功为 `false`，仅初始已关闭的冰箱这一项满足（1/5）。

证据在仓库外的 `../experiments/groceries_feasibility_20261019/interactive_cpu_run/`，包括 `run.json`、`events.jsonl`、`navigation_plans.jsonl` 和逐步 RGB 帧；GPU 失败记录在同目录的 `interactive_run/`。以上是人工选点的机制探针，不计为模型自主成功。

## 首轮模型实验（2026-10-20）

`gpt-6.1-sol / low`、规划 skill 开启、实例 301、seed 0、CPU 刚体、四路 RGB、无 demo motion 的自主回合保存在仓库外的 `../experiments/groceries_gpt61sol_low_planning_20261019_r1/`。[内网审阅首页](http://10.130.140.115:8769/groceries_gpt61sol_low_20261019_index.html) 链接五视角回放、连续视频和审计 JSON。模型读取了规划 skill，自己从 RGB 选点、打开室内门、取出购物袋、关闭后备箱，并把袋子放在门内木地板。它未能导航至厨房，最终用 `finish(blocked)` 如实报告失败。自定义 BDDL 独立评分为 **2/5**：后备箱和冰箱关闭；番茄、牛奶与空袋的三个位置目标均未达成。51 次模型动作中有 13 次 `navigation_unreachable`；连续视频与模型/模拟器调用对齐检查通过。

本轮**不能视为完整显式导航协议通过**：模型调用了 `navigate_to`，但执行器在六次 `open`、`close`、`grasp` 或 `place_on_top` 内也规划了路径，审计的 `all_paths_from_explicit_navigate_to=false`。后续先修复或重新定义该动作边界，并解决车库门口至厨房的可达性，再用新目录重跑；本轮原始记录保持不变。
