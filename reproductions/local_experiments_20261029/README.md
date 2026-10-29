# 本地实验代码与环境归档（2026-10-29）

本归档同时覆盖热饮、杂货、skill/协议对照、餐后收拾、摆桌/自助餐、野派清理和执行器/相机/传输诊断。它基于远端 `main@1174b3b8c1bf947b9d33876dbb193cb26f720b85` 新建，保存本地实验实际采用的版本，供复现、检查差异和后续移植。仓库根目录继续采用 main 的工具与执行器；这里的历史版本有独立入口。

清点的756个顶层目录包含180个缓存目录、1个认证状态目录和历史发布工作树，**不是756场自主实验**。缓存、凭据、完整环境、基础资产、原始会话和媒体未放入Git。收集按明确的目录规则、最多3层元数据路径遍历；已发现的源码树单独完整收集文本代码。每次运行的完整录像/日志保留在原存储，缺少证据的旧实验仍标缺失。

## 导航

| 文件/目录 | 用途 |
|---|---|
| [EXPERIMENT_UNIT.md](EXPERIMENT_UNIT.md) | 一场实验必须固定哪些代码、配置、环境和证据 |
| [MAIN_DIFFERENCES.md](MAIN_DIFFERENCES.md) | 与同事新main的工具、执行器、任务和评分差异 |
| [EXPERIMENT_INDEX.md](EXPERIMENT_INDEX.md) | 全部本地目录按实验族分类的入口 |
| [catalog.json](catalog.json)、[recorded_metadata.json](recorded_metadata.json) | 实验→代码快照→环境/任务/实际运行记录的机器可读索引 |
| [runtimes/](runtimes/) | 六份便于直接阅读的代表性源码，见下表 |
| [selected_runtimes.json](selected_runtimes.json) | 可读源码名称与完整SHA256快照ID |
| `snapshots/`、`objects/` | 历史版本的文件清单和按内容去重的gzip文件内容；可校验、恢复 |
| [environment/README.md](environment/README.md) | 已记录依赖、资产版本/哈希、环境迁移和剩余限制 |
| [upstream/](upstream/) | 两套实际上游快照的Git基点、20个覆盖文件及完整哈希 |
| [experiments/hot_drink_pair_20261026/](experiments/hot_drink_pair_20261026/) | 成功热饮对照的原计划、启动器、分析器、修复历史和轻量结果 |
| [validation/](validation/) | 归档恢复、输入检查和历史评分重算的实际验证结果 |

## 代表性代码版本

| 可读目录/恢复别名 | 实际来源 | 使用边界 |
|---|---|---|
| `local_development` | 本地维护分支未提交工作树，379文件 | 当前开发捕获，不能作为所有历史实验的版本 |
| `hot_drink_pair_20261026` | `hot_drink_lh_repaired_pair_20261026/source`，180文件 | 106/11与96/7的热饮自主配对；高层11目标 |
| `groceries_groc026` | `groc026_boundary_feedback_regression_20261025/execution`，181文件 | 杂货v2对应设施版本，任务/评分不同于热饮 |
| `remaining_four_20261027` | 同名批次`source`，214文件 | 后续范围修正与构造失败必须一起看，非四任务通过声明 |
| `table_buffet_20261027` | `table_buffet_runtime_20261027/source`，214文件 | 原生摆桌/自助餐候选与原生任务初态，不替代main task_designs |
| `wild_party_v68` | `wild_party_runtime_20261026/execution_source_v68`，214文件 | 本次捕获的原生野派候选；私有14/14不等于自主成功，原生GPU故障仍需看逐次记录 |

其他版本不只存在于路径字符串中：已收集的文本代码实际保存于`objects/`，`catalog.json`给出每个来源对应的快照ID。源码模块快照、skill快照、启动脚本和完整运行时代码候选分开标注，不能把只有`src`的记录当完整实验包。`recorded_manifest_comparison`若只是父目录参考清单，差异不被伪称为历史文件被修改。

## 无GPU核验与恢复

从本目录运行（Python 3.11）：

```sh
python tools/archive.py verify
python -m unittest discover -s tools -p test_archive.py -v
python tools/archive.py show groceries_v2
python tools/archive.py restore hot_drink_pair_20261026 --output /tmp/hot-drink-source
```

`restore`也接受`catalog.json`中的完整快照ID。它校验内容、拒绝路径穿越和覆盖已有目录。`runtimes`中旧测试需在各自源码/环境下运行，归档不会让它们混入main的默认pytest收集。

## 准备新的热饮对照工作目录

先准备BEHAVIOR上游Git仓库（需要记录的基点commit对象），按本归档还原精确代码：

```sh
python tools/recover_upstream.py --bundle hot_drink_20261026 \
  --git-checkout /path/to/BEHAVIOR-1K \
  --output /path/to/new-frozen-upstream
```

恢复器逐文件验证1393份输入；野派上游使用`--bundle wild_party_20261026`，对应1389文件。没有历史Git基点记录的野派快照明确把所选commit标为重建基点，最终身份由实际文件哈希证明。

然后使用自己的数据覆盖层、已配置模型服务目录和共享GPU锁目录：

```sh
python tools/prepare_hot_drink.py \
  --output /path/to/new-hot-drink-pair \
  --upstream /path/to/new-frozen-upstream \
  --dataset-overlay /path/to/dataset-overlay \
  --gpu 0 --gpu-uuid GPU-YOUR-ACTUAL-UUID \
  --codex-home /path/to/your-configured-model-home \
  --provider-profile YOUR_PROFILE --provider-host YOUR_PROVIDER_HOST \
  --lock-directory /path/to/shared-simulator-locks
```

这一步仅准备文件，不启动模拟器、不请求模型。已实际在新目录验证源文件、上游和两份场景输入匹配；运行时源码不改。启动器副本只调整安装路径/GPU、锁目录和回放输出位置，变更写入`migration.json`。准备好匹配环境和基础资产后，用该环境的Python运行新目录中的`supervisor.py`。数据目录、认证和原输出都不覆盖。

Blackwell异机复跑曾统一使用`--disable-tf32`对应的`NVIDIA_TF32_OVERRIDE=0`，原4090实验没有这项移植设置；按实际硬件记录，不能悄悄混为同一配置。每个新输出仍保留120动作/20000步、相同高层评分、先无后有和相同模型请求，模拟器日志/全部失败/录像留在外部新目录。

## 已验证范围

- 归档对象校验、无覆盖恢复、损坏文件拒绝和路径检查通过。
- 从Git基点与覆盖文件重新恢复两套上游，1393/1389文件全部与实际冻结清单匹配。
- 新目录热饮准备成功，180运行文件逐个一致；没有为归档启动新GPU或模型实验。
- 用归档的评分/分析代码重读已有两场原始事件，仍为11/11与106/11、96/7。
- 另一个会话在2026-10-27已完成master异机环境迁移及两场复跑：107/9与93/7，两组11/11。其TF32设置、客户端缺少伴随二进制的启动错误及同线程恢复都有独立记录，不能与原配对混成单因素对照。

环境克隆迁移已验证，尚未验证从公共依赖源全新安装完整环境。历史运行的外部模型请求身份有记录，但不保证服务端模型逐token固定。原生任务、私有探针、热饮高层评分和官方公共任务结果始终分开。
