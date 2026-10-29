# 热饮长程 skill 对照：实验整体复现清点

清点日期：2026-10-25（Asia/Shanghai）。这里只清点已报告的六场长程 skill 增量对照：完整 skill 组 seed 0/11/22，其他四项相同、移除长程 skill 组 seed 0/11/22。此前两轮 skill 修改、全部 skill 关闭单场作为补充实验单独归档，不进入六场主结论。

本次已实际整理代码与轻量配置，并核对真实运行记录；没有重新运行模拟器或上传 GitHub。**暂存包不是已经通过异机安装验收的发行版。**

## 1. 当前交付物及核对结果

- `package_staging/`：756 个文件、12,697,219 字节（约12.7 MB）。包括两套冻结源码、当前上游补丁、任务/场景、实际提示词、六场轻量记录、分析器与验证脚本。
- `INVENTORY.json`：10类组件的状态、来源和剩余缺口；`publication_ready=false`。
- `SOURCE_LINEAGE.json`：两组源码哈希和实际运行快照比对。
- `SIX_TRIALS.json`：六场结果、版本与收尾事实。
- `RAW_EVIDENCE_INVENTORY.json`：9,367 个原始文件、3,477,706,619 字节（约3.48 GB）的来源、大小和逐文件SHA256。没有为了清点复制全部视频和图像；发布时需另外归档并补公开链接与归档包哈希。
- `STAGING_MANIFEST.json`、`verify_staging.py`：当前代码暂存包的逐文件完整性检查。

已通过：两组254/252个冻结文件哈希；六场run与controller中的实际src快照与对应冻结源码一致；六场真实系统提示词各组内部一致；九工具集合一致；298项记录包版本在六场中全部相同；六场配置的修复后场景文件仍存在且哈希匹配；当前上游patch在基准文件上应用并与当前源文件内容逐项相同；离线表格可重算3/3与0/3；非法act审计回归通过。

## 2. 必须作为同一实验整体交付的代码

### A. 我们的 benchmark 运行时：完整冻结src树与运行脚本

来源：

- 完整 skill：`experiments/hot_drink_optimization_20261022/baseline_source_r3`，254文件。
- 无长程 skill：`experiments/hot_drink_long_horizon_ablation_20261024/execution`，252文件。

已复制到`package_staging/runtime/{with_long_horizon,without_long_horizon}/`。不能改为当前主工作区；后者存在这些实验之后的杂货修复和skill修改。

必须一起保留的模块包括：

| 模块 | 为什么是实验的一部分 |
|---|---|
| `executors/omnigibson_rgb.py` | 模型像素落点解析、同射线可见表面、抓取携带、载物闭包、放置验证/回滚、明确导航动作等行为 |
| `executors/gt_navigation.py`、`doorway_traversability.py`及其本地导入 | 地板/物体接近区分、通行图、候选点和跟随执行；不能只拿通用原版导航 |
| `omnigibson_backend.py`、`asset_preflight.py`、`task_bindings.py`及本地导入 | 兼容性检查、场景地板恢复、机器人配置、实例绑定、GPU水初始化与后续接触缓存处理 |
| `high_level_hot_drink.py`、`goal_grounding.py` | 高层操作状态与独立11项得分；不是原生水粒子评分 |
| `vision_harness.py`、`contracts.py`、`deadline.py`、`bridge.py` | 动作和仿真预算、无整场时限、意图校验、拒绝/停止/收尾语义 |
| `vision_policy.py`、`mcp_server.py`、`tools/`、`skill_runtime.py`、`clients/` | 真实提示词和工具schema、skill读取、模型传输与续跑 |
| 观察、surround、视频、记录与重放模块 | 四固定相机、渲染同步、成功/失败日志、连续五视图证据及调用对齐 |
| `scripts/run_no_demo_trial.py`、`run_demo_trial.py`、`run_codex_controller.py` | 外层环境、控制器参数、初始化/收尾watchdog、模型恢复机制 |
| `scripts/audit_demo_trial.py`、`analyze_workflow_efficiency.py`、review/publish依赖 | 独立重放、动作/请求分账和结果审计；保留原始失败审计 |
| `tests/`、`pyproject.toml`、运行源manifest | 为修复提供回归入口并定义源码身份 |

执行器使用私有地图、位姿、像素深度等执行所选动作，模型只接收允许的RGB与有限反馈。这是本实验的执行假设，不能换成另一物理控制器后仍声称相同实验。

### B. Skill与提示词：两组实际差异

四个共同skill：`failure-recovery`、`pick-and-place`、`visual-exploration`、`visual-manipulation`，主文档及所有references共9份文件保持相同。按需读取不是强制全部使用；实际读取行为保留。

完整组额外提供`long-horizon-planning/SKILL.md`与`references/bddl-goals.md`，并要求读取。消融组删除这两份文件和该提示词读取段。九个工具、规划/记忆接口和机器人调用前四字段decision保持一致。

- 完整bundle：`5fd7031b5e4b2398c27723137156011b9ee539eb8820ac3c62b16a132894edd2`。
- 无长程bundle：`5dcce20f01e9c4d37cd45e46a28093152fc1a5321ddd87df060b71cbf7b00996`。
- 实际提示词：`package_staging/configs/actual_prompts/`；逐场从controller记录的启动参数提取，不是事后重写。
- 差异：`actual_prompt_difference.patch`。

消融执行目录另有3处源码差异：`vision_policy.py`移除长程读取要求、`run_demo_trial.py`准确标记消融元数据、`audit_demo_trial.py`处理被拒绝的无primitive非法调用。后两项分别为记录和离线分析变化，不是机器人动作/评分修复。完整组历史执行字节不追溯修改。

### C. 上游BEHAVIOR/OmniGibson：仅commit不够

六场记录的上游commit均为`b1979916ec1549b10a4e65e630bc6504a9af1b00`，仓库`StanfordVL/BEHAVIOR-1K`，代码同时依赖其`OmniGibson/`、`bddl3/`、`joylo/`。

本次核对发现工作树相对该commit有**17个已跟踪文件修改及5个未跟踪代码/任务文件**。它们不包含在commit中，也不在我们的src快照里。当前内容已保存到：

- `package_staging/upstream/BEHAVIOR-1K_current_worktree.patch`。
- `current_changed_files/`：17个修改文件与5个未跟踪文件完整字节。
- `provenance.json`：每个文件的当前SHA256、mtime、基准版本及核验结果。

关键修改包括：`env_base.py`实现`OG_SWITCH_TO_CPU_AFTER_WATER`；`simulator.py`接触缓存开关；`sampling_utils.py`在射线和放置采样中统一Tensor设备/dtype；控制器、实体、机器人和USD处理中的兼容改动。不能认为我们自己的runner设置一个环境变量就能让原版上游具有对应实现。

未跟踪的`omnigibson/tasks/partial_order_behavior_task.py`尤其不能漏：已修改的`tasks/__init__.py`会直接导入它。即使本次评分不用该可选类，省略文件也可能使模块导入失败。

**历史缺口：** 六场未保存上游dirty patch或逐文件哈希。当前文件mtime都早于对照运行，是辅助线索，不是历史同一性证明。当前patch已验证可以重建当前文件，仍不能被描述为“已认证的六场历史上游源码”。应在发布说明中保留这一限制，或通过更早归档补证；若没有补证，后续基于这个新完整冻结运行包的实验必须标为新复现，不能补写成历史原样运行。

## 3. 必须固定的任务、场景和资产

已暂存：实际instruction、高层BDDL、原生BDDL供协议区别、实例301初态、partial/full场景模板、场景安装/构建逻辑、任务目录及生成后的修复场景。`configured_scene/`包含与六场环境配置哈希匹配的场景内容。这些不能因原路径位于appdata就当作无关缓存删除。

场景修复包含从同一完整模板补入缺失固定地板；导航读取预计算地图，侵蚀和选点规则由运行时代码定义。地图会改变路径与可达性，也是benchmark输入。当前6份小地图PNG和8份小元数据已在`asset_inventory/`保存并标为当前捕获，历史缺少独立哈希的部分没有冒充历史冻结。

大型外部资产还包括：

- `behavior-1k-assets` 3.9.0：对象USD、碰撞几何、纹理/材质、场景布局、metadata、water等系统支持。
- `omnigibson-robot-assets` 3.8.2：r1pro及其完整依赖。
- `2026-challenge-task-instances`：除已冻结覆盖文件外的必要元数据/场景引用。
- Isaac Sim自带资源及扩展；上游`eval/r1pro.yaml`和`gello/utils/og_teleop_cfg.py`属于固定源码依赖。

已根据partial/full模板枚举374个保守的模型候选目录，但这是完整模板的超集，不是经加载追踪认证的最小资产闭包。原overlay中的共享目录/软链接不会自动变成别人的可用资产。正式交付需提供可获得的基础资产版本、必要覆盖文件、全部依赖的校验清单；若不具备分发条件，提供安装获取说明和可校验来源。**不上传`omnigibson.key`。**

## 4. 不能遗漏的运行条件和模型客户端

已记录并核对：六场298项包版本相同；OmniGibson3.9.2、IsaacSim5.1.0.0、Torch2.7.0+cu128、BDDL3.7.0、MCP1.28.1；Codex CLI0.157.1。模型为服务商`api.gpt.ge`提供的`gpt-6.1-sol`、low。仿真seed控制本地随机性，不保证远程模型逐token重现。

必须保留：120 act/look、20000环境步、无整场墙钟截止、900秒初始化、180秒收尾监督；3次unfinished续跑、3次rate-limit恢复、120秒退避、2次history重启。初始化GPU、之后转CPU以及接触缓存重新启用是实际运行流程，单看run.json里的`dynamics_mode=gpu`不能完整表达阶段变化。

完整开关清点见`configs/runtime_contract_inventory.json`与`environment_read_sites.json`。现有runner复制`os.environ`，部分OG/MAS开关可能从父环境继承；六场并未记录完整生效环境，因此不能仅凭plan推断其余开关都未设置。发布的新启动器应显式清理/白名单化这些变量，但这属于消除新复现环境歧义，不能反推历史值。

当前`pyproject.toml`不是完整环境锁，skill数据文件安装列表也不全。源码原样保存用于身份核验；发布安装入口需额外补齐所有skill资源、wheel/渠道和可编辑上游依赖。298项版本列表不是已测试安装配方；OS、GPU驱动、Kit扩展/渲染组件和完整启动环境仍待完善。当前所有原始命令含本机绝对路径，需要新可迁移入口解析相对路径及用户外部资产目录。

## 5. 证据和分析也必须打包

代码清单不能替代证据清单。六场原始目录应全部归档，包括成功与失败的模型输出、工具调用/返回、状态轨迹、captures/RGB、navigation/base/carry记录、skill快照、配置、评分、审计和五视图连续视频。可将约3.48GB原始材料单独存储，仓库保存公开的固定版本链接与SHA256；私网回放URL不是外部复现下载渠道。

两个必须公开的偏差：seed0模型未正式收尾，由监督器aborted；seed11有三次机器人接口外的客户端资源查询（两个不存在服务器错误及一次空列表）。原始全调用对齐失败标志保留；过滤明确外部调用后的机器人接口对齐不能覆盖原始失败。其他四skill可用与实际被读取分开陈述。

`validation/recompute_recorded_table.py`只从已记录评分重算结果表；要重新独立重放评分，仍需要完整events与high_level_state原始日志。`validation/audit_regression_check.py`覆盖非法act缺primitive的真实审计模式。正式安装/真实物理运行未在本次清点中执行。

文档必须附：bugfix_journal、skill迭代记录、任务工作流、benchmark套件及高层/原生差异说明。当前文档包含更晚的杂货修复，必须通过冻结manifest区分是否进入六场；不能把所有最新修复都说成热饮成功条件。

## 6. 发布前还剩什么

| 项目 | 当前状态 | 发布需要完成的工作 |
|---|---|---|
| 我们的源码、skill、实际prompt、任务与修复后场景 | 已核验并暂存 | 保持字节与哈希，不拿最新工作区替代 |
| 上游patch与未跟踪导入文件 | 当前快照及应用验证完成 | 补历史同一性证据，或明确历史限制并冻结新复现版本 |
| 大型资产与地图/元数据 | 版本和路径清点；部分小文件当前保存 | 取得可移植资产、完整闭包哈希和外部获取说明 |
| 安装环境与运行入口 | 298包版本及开关清点 | 可安装锁/镜像、固定驱动及渲染条件、消除绝对路径与隐式父环境 |
| 六场完整原始证据 | 来源/大小已清点，轻量材料已保存 | 逐文件SHA256已完成；仍需脱敏复核、外部归档、归档包哈希及公开下载链接 |
| 新机器验收 | 未执行 | 导入→加载场景→四相机→动作/收尾→评分/审计的冒烟测试；若要复现性能再按完整六场方案运行 |

源码补丁/资产的发布许可证与NOTICE需随最终发行方案明确；当前主仓库未发现顶层LICENSE文件。不要上传API认证文件、许可证密钥、账户原生session、scratch/cache。保留脱敏和排除清单，不删除原始内部证据。

因此，准确的结论是：**实验整体需要“修复后的运行时＋上游兼容补丁＋任务/资产＋显式环境＋skill条件＋独立分析＋六场证据”一同交付。现已完成逐项清点和代码暂存；尚不能宣称异机复现包已经验收。**
