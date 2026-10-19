# 长程偏序机器人任务：构建到审阅的 workflow

本流程以 `prepare_and_serve_hot_drink` 为已落地样例，供下一项跨房间、多物体、有前后依赖的任务复用。它区分三件容易混在一起的东西：**人类指令**告诉机器人要完成什么；**任务定义和场景**规定初态与可验证的终态；**实验 harness**规定动作怎样执行、怎样计分。规划 skill 只帮助机器人管理自己的假设和步骤，不替代任务定义或独立评分。

## 1. 先写人的目标，再设计偏序

产物是 [`instruction.txt`](../tasks/prepare_and_serve_hot_drink/instruction.txt)、[`README.md`](../tasks/prepare_and_serve_hot_drink/README.md) 和 [`partial_order.json`](../tasks/prepare_and_serve_hot_drink/partial_order.json)。指令保持简短，只说明目标与机器人无法从初始画面得知的物品位置；不告诉机器人动作顺序。本例原文是：

> 请准备一份热饮和点心：把冰箱里的水烧开，橱柜里有水壶、杯子、餐巾、餐垫、盘子、茶匙、托盘、糖饼干和蜂蜜；将饮品、餐具和点心布置在客厅茶几上。

在作者侧列出每个最终可观察关系，并画出**真正的因果前提**。本例的水链是“取得唯一水源 → 将水倒入水壶 → 烧开 → 倒入杯中 → 上桌”；取餐具和布置茶几可以与水链交错。不要把每件物品简单串成固定动作清单，也不要增添人没有要求的水瓶最终位置。`partial_order.json` 是可从最终 BDDL 目标追踪的里程碑图：它没有单独的 `retrieve_water` 目标索引，因为取水是烧水的因果前提，不是终态谓词。其可选评分器是 [`PartialOrderBehaviorTask`](../tasks/prepare_and_serve_hot_drink/optional_evaluator/partial_order_behavior_task.py)；目前热饮视觉正式回合**没有**用该评分器。

完成门槛：每个叶目标都有明确的终态证据；依赖图无环；并行分支没有被人为写死先后；人类指令没有泄露规划答案。

## 2. 固定场景、实例和真实物体

在 [`available_tasks_entry.yaml`](../tasks/prepare_and_serve_hot_drink/available_tasks_entry.yaml) 指定场景与房间，在 [`scene_layout.json`](../tasks/prepare_and_serve_hot_drink/scene_layout.json) 记录预期物体位置与门的初态，再制作 `scene/` 中的模板与实例状态。本例固定 `house_single_floor` 的厨房和客厅，实例号 301；水瓶在关闭的冰箱中，水壶、杯子、餐具和点心在初始打开的橱柜中，茶几在客厅。橱柜初态必须同时与场景实例、机器人 RGB 和高层状态一致。

模板中的物品必须是实际加载的可见资产，不能只在 BDDL 或符号状态里出现。核对对象绑定、资产类别、实例位姿、门状态、初始水源，并在首帧四路 RGB 和第三视角确认。安装到外部 BEHAVIOR checkout 用 [`install.py`](../tasks/prepare_and_serve_hot_drink/install.py)；写入前检查目标路径是否已有同名任务，写入后运行 `--check` 验证文件与 `available_tasks.yaml` 条目一致：

```bash
python tasks/prepare_and_serve_hot_drink/install.py \
  --behavior-root ../behavior_challenge_sunyujie/BEHAVIOR-1K
python tasks/prepare_and_serve_hot_drink/install.py \
  --behavior-root ../behavior_challenge_sunyujie/BEHAVIOR-1K --check
```

完成门槛：所有目标物体真实存在、可被 RGB 看到或经开门/行走后看到；初始水与门状态相符；机器人有可行的房间通路。

## 3. 分清原生任务与高层实验协议

[`problem0.bddl`](../tasks/prepare_and_serve_hot_drink/problem0.bddl) 是带物理水粒子初态的原生任务定义。若要报告 BEHAVIOR 官方成绩，必须让官方目标在仿真状态上成立，用原生评分判定。

当前评估**高层规划能力**的视觉回合使用 [`high_level_visual.bddl`](../tasks/prepare_and_serve_hot_drink/high_level_visual.bddl)：它保留场景对象与目标结构，但不生成初始水粒子。启动脚本为每个回合建立独立 BDDL overlay；[`HighLevelHotDrink`](../src/manipulation_agent/high_level_hot_drink.py) 以高层关系处理液体、烧水和摆放，并给出 11 项语义目标。该协议的 `official_task_success=null`，不能称为官方 BDDL 通过，也不能与物理回合混算。两种协议及已知失配见 [原生与高层差异](behavior_native_vs_hot_drink_20261017.md)。

完成门槛：预先选定**一种**评分协议；报告里保留 BDDL SHA、实例号和协议名；执行反馈只确认本次操作，整任务成绩由独立评分给出。

## 4. 从目标反查原子动作是否真的可执行

先运行 [`check_atomic_action_coverage.py`](../scripts/check_atomic_action_coverage.py) 静态检查目标谓词，再对真实实例做动作级验证：每类目标物体是否可选点、可接近、可抓取、可放置；水瓶能否倒入水壶；水壶置于灶台后 `boil` 是否返回 `water_boiled=true`；热水能否倒入杯中。静态谓词映射**不能**证明像素、距离、场景资产和物理执行都可行。

```bash
python scripts/check_atomic_action_coverage.py \
  ../behavior_challenge_sunyujie/BEHAVIOR-1K/bddl3/bddl/activity_definitions \
  --task prepare_and_serve_hot_drink
```

机器人公开接口是 [`act`](../src/manipulation_agent/tools/action.py)：模型从最新 `image_ref` 选归一化 `point`，自己调用 `navigate_to`；抓取、放置、倒水、烧水不能偷偷替它导航。动作名来自全局 [`PRIMITIVES`](../src/manipulation_agent/executors/primitives.py)。目前 `boil` 在全局目录里，但实际成功实现位于 [`RGBBackend` 的热饮高层分支](../src/manipulation_agent/executors/omnigibson_rgb.py)；这是现存耦合，迁移任务时不能误认所有任务都有通用烧水动作。`carry.py`、`placement.py` 和 `omnigibson_backend.py` 中的抓取、放置、物理倒水等属于更通用的执行基础，改动前需跑其他任务回归。

完成门槛：每项最终关系至少有一条在**当前协议和场景**中验证过的动作路径；失败反馈包含可恢复信息；不通过按名称目标代替 RGB 选点。

## 5. 接入规划 skill，但不把任务答案放进提示词

给模型加载 [统一长程规划 skill](../skills/long-horizon-planning/SKILL.md)，使它自己从指令、RGB 和工具反馈建立目标 DAG。`update_plan` 保存当前回合的子目标与依赖，`remember/recall` 保存可见事实和不确定性；每次工具调用附 `decision={state,active_subgoal,reason,expected}` 公开说明。计划是模型的假设，不是评分真值。BDDL 输入可参考 skill 内的 `references/bddl-goals.md`。不要重新启用旧 `task-decomposition` 文件或开关。

完成门槛：模型实际读取 skill；初次计划把关键物品和独立分支列清；完成、失败或目标变化后修订 DAG；不得把所有剩余物品合成一个无法核对的粗节点。

## 6. 固定协议运行，保存完整证据

正式视觉回合使用 [`run_no_demo_trial.py`](../scripts/run_no_demo_trial.py)；完整参数、资源与监控见 [实验交接指南](experiment_handoff_guide_20261014.md)。下面是本例的核心参数；`--output` 每次换新目录，provider 与 GPU 按现场配置填写：

```bash
MAS_DEMO_MOTION=0 OG_SKIP_PHYSICS_STEPS=0 \
OG_KINEMATIC_NAV_TELEPORT=0 MAS_NAV_RELAXED=0 \
python scripts/run_no_demo_trial.py \
  --task prepare_and_serve_hot_drink \
  --instruction "$(cat tasks/prepare_and_serve_hot_drink/instruction.txt)" \
  --bddl-source tasks/prepare_and_serve_hot_drink/high_level_visual.bddl \
  --high-level-harness --long-horizon-planning-skill on \
  --model gpt-6.1-sol --reasoning-effort low \
  --max-actions 120 --max-sim-steps 20000 --timeout 14400 \
  --output ../experiments/<new-run-name> \
  --omnigibson-source ../behavior_challenge_sunyujie/BEHAVIOR-1K/OmniGibson \
  --provider-profile <configured-profile> --codex-home <isolated-codex-home> \
  --expected-provider-host <configured-host> --gpu <free-gpu>
```

`OG_SKIP_PHYSICS_STEPS=0` 和 `OG_KINEMATIC_NAV_TELEPORT=0` 保证连续控制步与实际行走轨迹。`reasoning-effort=low` 是本实验约定；提高档位或修改预算会引入新变量。120 是 2026-10-18 试验的预算，不是完成任务的保证。运行期间核对模型读取 skill、`update_plan` 写入、每步 `decision`、显式导航、动作失败后的策略变化和剩余预算；不要因为日志短时间安静就误判为结束。

完成门槛：`summary.json` 记录模型、档位、预算、实例、指令、skill 开关；控制器正式结束；原始 `events.jsonl`、RGB、状态日志和连续视频完整保留。

## 7. 独立审计、发布与比较

用 [`audit_demo_trial.py`](../scripts/audit_demo_trial.py) 重放成功动作，核对 `high_level_state.jsonl`、模型与模拟器调用、每次公开决策、显式导航、底盘高度和视频。失败回合也要保留完整 HTML，不能以模型的 `finish(achieved)` 或工具 `ok=true` 代替独立目标判定。

```bash
PYTHONPATH=src python scripts/audit_demo_trial.py ../experiments/<run-name>
```

该脚本生成 `run/replay.json`、`run/review_5view.html` 和 `demo_audit.json`。再按 [实验交接指南第 6 节](experiment_handoff_guide_20261014.md) 用 `publish_demo_trials.py` 发布内网 index，`curl -I` 检查首页、完整审阅页和视频均为 HTTP 200。最终交付写明任务成功与否、11 项高层关系、动作预算使用、模型计划更新次数、主要失败类型、视频时长与内网 URL。

比较 skill 效果必须有同实例、同模型、同档位、同指令、同预算、同原子动作接口的有效对照。2026-10-18 的 skill 回合是 120 次动作、8/11；旧 80 次回合与它预算不同，旧 120 次回合审计失败，所以**目前不能据此认定 skill 带来稳定提升**。该回合 DAG 后段未持续更新，模型还用 `wait` 消耗预算；这类行为应作为规划缺陷报告，不能被总体分数掩盖。

## 文件边界与提交范围

| 层 | 本例文件 | 迁移新任务时的处理 |
| --- | --- | --- |
| 任务包 | `tasks/prepare_and_serve_hot_drink/`：指令、BDDL、偏序、场景、安装器 | 换任务名和目标，重新制作实例，不复用旧绑定 |
| 任务注册 | `src/manipulation_agent/tasks.json` | 加新目录项，指令与 `instruction.txt` 保持一致 |
| 任务专属高层协议 | `src/manipulation_agent/high_level_hot_drink.py`、`executors/omnigibson_rgb.py` 中的热饮分支 | 抽成独立任务适配器或明确继续使用原生评分；不要复制全局执行器分支 |
| 通用动作与运行时 | `tools/action.py`、`executors/primitives.py`、`vision_harness.py`、`run_demo_trial.py` | 只增加真正通用的动作；其他任务必须回归 |
| 规划与审计 | `skills/long-horizon-planning/`、`scripts/audit_demo_trial.py`、`scripts/build_review.py` | 复用流程，审计字段与新协议对应 |

当前工作树里的 `tasks/`、skill 和部分脚本仍未作为一组独立提交。准备 push 前应先核对 `git status`，按上述层次整理提交，并确保实验输出、凭据和大体积录像不进入源码提交。
