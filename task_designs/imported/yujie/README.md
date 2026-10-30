# yujie 已构建任务

这里只纳入两项已构建任务。定义文件从 [PR #6](https://github.com/echo636/agentic_mobile_manipulation/pull/6) 的固定提交 `bbe9b5f230cae81c58bc945ccb15aaaf4c01a206` 原样提取，来源为 `runtimes/local_development/tasks/`。
[provenance.json](provenance.json) 保存每个文件的源路径、SHA-256 和固定版本链接，以及未复制的场景、构建器和历史任务卡入口。

任务包包含任务定义、英文输入、杂货场景构建器和热饮场景恢复入口；执行器接入状态单独记录。模型指令原文保留中文以便核对历史，新实验使用旁边的 `instruction_en.txt`，翻译不增加操作步骤或隐藏目标。

## 热饮与点心

`prepare_and_serve_hot_drink`：从冰箱取水，准备热饮；从橱柜取餐具与点心，将饮品、餐具和点心布置到客厅茶几。任务包含饮品制作与餐具/点心准备两条依赖链，最后汇合到招待布置。

- [英文指令](prepare_and_serve_hot_drink/instruction_en.txt)与[原始指令](prepare_and_serve_hot_drink/instruction.txt)
- [历史视觉实验使用的定义](prepare_and_serve_hot_drink/high_level_visual.bddl)
- [作者侧依赖图](prepare_and_serve_hot_drink/partial_order.json)与[场景布局](prepare_and_serve_hot_drink/scene_layout.json)

已有完整场景及自主回合，但历史正式视觉实验使用 `HighLevelHotDrink`：倒水由高层水量状态记录，加热由高层状态转换处理。其 11/11 不能解释为原生水粒子和原生加热流程全部成功。可选偏序评分器未用于这些正式视觉回合，本次也未接入。

场景修复版本为 `complete_static_shell_v2`；完整模板、房间筛选模板和 instance301 状态的固定来源见 provenance。[恢复入口](prepare_and_serve_hot_drink/restore_scene.py) 从已获取的 PR 提交恢复三个原始场景文件并核对哈希，输出到 Git 以外：

```bash
# 从本仓库根目录执行；先确保本地 Git 已有 provenance 指定的 PR 提交。
git fetch origin refs/pull/6/head:refs/remotes/origin/review-pr6
python3 task_designs/imported/yujie/prepare_and_serve_hot_drink/restore_scene.py \
  --archive-repo . --output /absolute/empty/output/hot_drink_scene
```

[任务注册条目](prepare_and_serve_hot_drink/available_tasks_entry.yaml)和[原生定义](prepare_and_serve_hot_drink/problem0.bddl)也已保留。原生定义与高层视觉定义不是同一评分协议，原生定义不继承高层实验成功。这里不执行原作者会原地写入共享 BEHAVIOR checkout 的安装器；高层状态机及静态建筑恢复钩子留待 PR 后续审查。

## 两袋杂货

`carry_in_and_put_away_groceries`，以 `v2_design` 为收录版本：从汽车后备箱搬入两袋、六件食品。四件冷藏食品入冰箱，两包饼干入厨房吊柜，空袋放冰箱旁，后备箱、冰箱及两个吊柜关闭。两包饼干可任选合法柜子，也可分柜。

- [英文指令](carry_in_and_put_away_groceries/v2_design/instruction_en.txt)与[原始指令](carry_in_and_put_away_groceries/v2_design/instruction.txt)
- [原生自定义 BDDL](carry_in_and_put_away_groceries/v2_design/problem0.bddl)
- [历史开发/评估划分](carry_in_and_put_away_groceries/v2_design/evaluation_split.json)

已有构建叠加层及真实自主回合。作者归档中的 `groceries_v2_robust_gpt61sol_low_20261021_r1` 为 gpt-6.1-sol / low，105 次 act/look，自定义原生 BDDL 12/12；后续版本也有失败，不能将旧成功移算到当前执行器或宣称稳定成功率。`official_task_success=null`，这是自建任务成绩。

公开 `carrying_in_groceries` 实例只有一袋和两件食品，不能只替换 BDDL 就启动本任务。[场景构建器](carry_in_and_put_away_groceries/v2_design/build_scene_overlay.py) 已原样合入：它增加第二袋、食品与双柜绑定，并将袋子垂直尺寸调为原来的 1.2 倍。输入是已授权安装的 OmniGibson 数据集根目录，包含 `2026-challenge-task-instances/`；输出为独立叠加层，原始数据不写回。

```bash
python3 task_designs/imported/yujie/carry_in_and_put_away_groceries/v2_design/build_scene_overlay.py \
  --source /absolute/installed/omnigibson/dataset \
  --output /absolute/empty/output/groceries_dataset
```

默认零偏移、袋高 ×1.2 对应所收录的开发配置。构建器产生场景文件，不负责替换当前环境的 BDDL 或启动模型。第一版单袋任务属于该任务的历史版本，不另计数量。

## 接入范围

本次整合范围是两个已构建任务的定义、输入、场景准备工具和来源；模型 skill、执行器、评分实现以及六份 runtime 的其他内容仍待审查。原作者 README 和任务卡可通过 provenance 中的固定版本链接查看；其中历史状态、预算和路径不自动成为当前系统的配置。

2026-10-30 已用本机实际安装的数据集运行杂货构建器，生成双袋六食品、双柜绑定的独立叠加层；热饮三个场景文件已按源哈希恢复。产物及验证记录在共享根目录 `operations/custom_task_catalog_20261030/`，这验证的是文件准备，不是新仿真加载或自主成功。
