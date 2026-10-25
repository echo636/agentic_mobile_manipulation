# 热饮长程 skill 对照：冻结代码与复现材料

此目录集中保存已报告六场实验的代码、配置和轻量证据。主对照为完整五 skill 历史组 seed 0/11/22，与保留其他四项、移除长程 skill 的三场。记录结果为 3/3 与 0/3；历史参照、每组仅三场及协议偏差使其属于描述性证据，不能据此声称严格因果证明或泛化。此前全部关闭 skill 的单场和其他候选迭代不进入这个分母。

**状态：已归档代码与配置；完整异机运行复现尚未验收。** `INVENTORY.json` 中 `publication_ready=false` 表示完整复现发行版的验收状态，不表示本目录内的哈希检查失败。

## 从这里开始

| 路径 | 内容 |
|---|---|
| [REPRODUCTION_INVENTORY.md](REPRODUCTION_INVENTORY.md) | 完整依赖清点、逐层说明与剩余缺口 |
| `package_staging/runtime/with_long_horizon/` | 完整 skill 组冻结源码，254 文件 |
| `package_staging/runtime/without_long_horizon/` | 仅移除长程 skill 的冻结源码，252 文件 |
| `package_staging/upstream/` | 上游 commit、当前 17 文件补丁、5 个未跟踪文件及哈希 |
| `package_staging/task/`、`dataset_overlay/`、`configured_scene/` | 任务定义、初态、场景模板与实际修复后场景；后三者均位于 `package_staging/` 下 |
| `package_staging/configs/`、`environment/` | 实际提示词、工具、预算、启动计划、298 项依赖记录；均位于 `package_staging/` 下 |
| `package_staging/evidence_metadata/`、`results/` | 六场轻量记录、失败审计与结果；均位于 `package_staging/` 下 |
| [SOURCE_LINEAGE.json](SOURCE_LINEAGE.json) | 冻结源码与六场实际运行快照核验 |
| [RAW_EVIDENCE_INVENTORY.json](RAW_EVIDENCE_INVENTORY.json) | 9,367 个原始证据文件的本地来源、大小及 SHA256 |
| [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) | 上游来源与随附许可 |

本分支根目录的通用 `src/` 不是这六场的源码身份。复查本实验请使用这里的两套 `runtime/`，不要混入其他分支或当前工作区后续修复。四项共同 skill 的 9 个资源文件字节相同，九个工具与调用前公开意图机制均保留；完整组额外提供长程 skill 并要求读取。按需可用不等于模型实际读取全部 skill。

## 无 GPU 的离线核验

从本目录执行：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 package_staging/validation/audit_regression_check.py
PYTHONDONTWRITEBYTECODE=1 python3 package_staging/validation/recompute_recorded_table.py
python3 verify_staging.py
```

上述检查分别覆盖非法 act 的审计回归、从记录评分重算结果表、全部 756 个暂存文件的大小和 SHA256。重算结果表不等于从完整原始日志重新独立评分，也不是重新运行机器人。`verify_staging.py --require-publish-ready` 当前预期返回退出码 2。

## 完整运行复现的已知缺口

1. 六场只记录上游 commit，没有冻结当时的 dirty patch。当前恢复补丁已验证可应用并重建当前文件，但其历史同一性未被证明。
2. 大型 USD、纹理、机器人与系统资产未随代码上传；`asset_inventory/` 的版本和候选路径不是完整资产闭包。小地图/元数据中未有历史哈希的文件明确标为当前捕获。
3. 298 项包版本不是经过干净安装验证的锁文件；GPU 驱动、Kit 扩展及完整继承环境仍未固定。原计划保留原始绝对路径，需要可迁移入口。远程模型别名也不保证逐 token 重现。
4. 约 3.48 GB 六场原始日志、图像和录像仍在原存储，未纳入 Git。逐文件 SHA256 已归档，公开下载地址及脱敏后的外部证据归档尚未提供。私网回放地址不能当作公开下载入口。
5. 无长程组 seed0 由监督器 aborted 收尾；seed11 有额外客户端资源查询。原始全调用对齐失败标志保留，不以过滤后的机器人接口对齐覆盖。

恢复当前上游快照需使用 `provenance.json` 指定的基准 commit、应用 patch，再补入列出的五个 untracked 文件，并逐文件验证 `sha256_current`。这只能恢复当前捕获的上游，不会补上历史证明缺口。

`collect_inventory.py`、`complete_staging.py`、`hash_raw_evidence.py` 是原主机的收集/核对记录，依赖原始存储路径，不是安装器或实验启动器；不要在此归档上重新运行收集脚本覆盖内容。原始路径保留作来源线索，不要求读者在同名目录安装。

`INVENTORY.json` 和 `FINAL_CHECKS.json` 保留本地清点时的状态，前者的 `uploaded=false` 是清点当时的历史值；本分支上传的是这些材料的归档副本。`PUBLICATION_CHECKS.json` 记录本分支副本的再次核验。没有修改历史分数、源码、skill 或失败证据，没有为此次代码归档启动新模型实验。
