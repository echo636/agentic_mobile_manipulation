# yujie 已构建任务

这里只纳入两项已构建任务。定义文件从 [PR #6](https://github.com/echo636/agentic_mobile_manipulation/pull/6) 的固定提交 `bbe9b5f230cae81c58bc945ccb15aaaf4c01a206` 原样提取，来源为 `runtimes/local_development/tasks/`。
[provenance.json](provenance.json) 保存每个文件的源路径、SHA-256 和固定版本链接，以及未复制的场景、构建器和历史任务卡入口。

这些是任务定义资料，不是已安装的当前执行器插件。模型指令原文保留中文以便核对历史；后续新实验按项目要求使用单独记录的英文输入。

## 热饮与点心

`prepare_and_serve_hot_drink`：从冰箱取水，准备热饮；从橱柜取餐具与点心，将饮品、餐具和点心布置到客厅茶几。任务包含饮品制作与餐具/点心准备两条依赖链，最后汇合到招待布置。

- [原始指令](prepare_and_serve_hot_drink/instruction.txt)
- [历史视觉实验使用的定义](prepare_and_serve_hot_drink/high_level_visual.bddl)
- [作者侧依赖图](prepare_and_serve_hot_drink/partial_order.json)与[场景布局](prepare_and_serve_hot_drink/scene_layout.json)

已有完整场景及自主回合，但历史正式视觉实验使用 `HighLevelHotDrink`：倒水由高层水量状态记录，加热由高层状态转换处理。其 11/11 不能解释为原生水粒子和原生加热流程全部成功。可选偏序评分器未用于这些正式视觉回合，本次也未接入。

场景修复版本为 `complete_static_shell_v2`；完整模板和 instance301 状态的固定来源见 provenance。后续移植需要明确保留高层规划协议，还是另建原生物理版本；两种协议的成绩分开统计。

## 两袋杂货

`carry_in_and_put_away_groceries`，以 `v2_design` 为收录版本：从汽车后备箱搬入两袋、六件食品。四件冷藏食品入冰箱，两包饼干入厨房吊柜，空袋放冰箱旁，后备箱、冰箱及两个吊柜关闭。两包饼干可任选合法柜子，也可分柜。

- [原始指令](carry_in_and_put_away_groceries/v2_design/instruction.txt)
- [原生自定义 BDDL](carry_in_and_put_away_groceries/v2_design/problem0.bddl)
- [历史开发/评估划分](carry_in_and_put_away_groceries/v2_design/evaluation_split.json)

已有构建叠加层及真实自主回合。作者归档中的 `groceries_v2_robust_gpt61sol_low_20261021_r1` 为 gpt-6.1-sol / low，105 次 act/look，自定义原生 BDDL 12/12；后续版本也有失败，不能将旧成功移算到当前执行器或宣称稳定成功率。`official_task_success=null`，这是自建任务成绩。

公开 `carrying_in_groceries` 实例只有一袋和两件食品，不能只替换 BDDL 就启动本任务。历史构建器增加第二袋、食品与双柜绑定，并将袋子垂直尺寸调为原来的 1.2 倍；构建器固定来源见 provenance。迁移时需要恢复对应场景叠加层并适配当前运行入口。第一版单袋任务属于该任务的历史版本，不另计数量。

## 接入范围

本次只整理定义与来源，不改模型 skill、执行器、评分或现有实验队列，也不复制六份 runtime。原作者 README、任务卡及构建脚本可通过 provenance 中的固定版本链接查看；其中历史状态、预算和路径不自动成为当前系统的配置。
