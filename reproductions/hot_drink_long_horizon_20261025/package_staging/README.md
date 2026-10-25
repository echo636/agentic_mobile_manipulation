# 热饮长程skill对照：复现材料暂存包

这是逐项清点生成的代码/配置/轻量证据包，尚不是通过干净机器验证的可运行发行版。目录外的INVENTORY.json、SOURCE_LINEAGE.json和RAW_EVIDENCE_INVENTORY.json说明范围与缺口。

主结论范围：完整五skill历史组3个seed，与其他四skill不变、移除长程skill的新组三个seed。不是全部skill关闭实验；不是原生水粒子物理任务或官方成绩。

- runtime/：两组实际冻结的src、scripts、skills、tests和pyproject，分别254/252文件；未修改历史字节。完整src必须保留，不能只摘执行器。
- upstream/：BEHAVIOR-1K基准commit加当前17文件修改patch、5个未跟踪文件；补丁应用及内容哈希已验证。历史未保存上游工作树哈希，当前捕获不冒充历史认证。
- task/、dataset_overlay/、configured_scene/：实际任务、场景模板、实例、目录及运行时修复后的场景。
- asset_inventory/：大型外部资产、模型候选路径、版本及当前小地图/元数据；没有复制模型、材质、纹理、机器人和全部系统资产。不得上传许可证密钥。
- configs/：原始启动计划（含本机绝对路径，需迁移）、六场真实提示词、工具列表、启动开关和环境读取点。
- environment/：六场记录一致的298项包版本及客户端版本。不是已通过安装的依赖锁。
- evidence_metadata/、results/：六场原始评分、审计、运行配置和对照结果；原始失败标志保留。
- validation/：纯CPU审计缺primitive回归，及从已记录评分重算表格；不等同物理重跑或从完整日志独立重放评分。
- documentation_current/：当前研究日志，仅供追溯；含较新杂货修复，不代表这些修复都进入本轮。

离线核对（无GPU）：

```bash
python3 validation/audit_regression_check.py
python3 validation/recompute_recorded_table.py
```

发布前仍需补齐：历史上游patch一致性说明、外部资产及哈希、明确环境和安装方案、移除绝对路径的启动器、六场大体积证据的公开存储与脱敏、干净机器验收。父目录中verify_staging.py只验证当前暂存包完整性，不能认证这些缺口已解决。
