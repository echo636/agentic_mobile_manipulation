# 环境、资产与异机复跑记录

代码恢复成功与完整环境复现是不同验收。这里保留了实际记录，并明确哪些输入仍在外部。

| 材料 | 内容与范围 |
|---|---|
| recorded_python_distributions.json | 已收集的626份逐run依赖记录去重后相同的分发版本表；没有记录的旧回合不能据此补猜 |
| conda_explicit.txt / pip_freeze.txt | 热饮异机迁移时导出的实际环境。pip文件含editable/local路径和CUDA专用wheel，不能当未经调整即可全新安装的requirements |
| client_bundle_manifest.json | codex-cli0.157.1的codex和codex-code-mode-host两个二进制大小/哈希；二进制与认证不入Git |
| base_assets_manifest.json.gz | 103841个基础资产文件哈希/大小；记录behavior-1k-assets3.9.0、robot-assets3.8.2 |
| dataset_overlay_manifest.json.gz | 32423个覆盖层/任务输入文件哈希/大小；不包含许可证密钥 |
| migration_record.json | 2026-10-27 master迁移所改路径、GPU和TF32等实际差异 |
| environment_cache_correction.json | Conda缓存与pip覆盖文件不一致的迁移修正，不能把旧包缓存当实际已安装文件 |
| current_host_capture_20261029.json | 本次整理时的Python/平台/GPU/驱动，只代表当前主机，不追溯覆盖历史 |
| REPRODUCTION_REPORT.md等 | 另一会话已完成的异机复跑及客户端基础设施事件限制 |

热饮记录的主要组件为Python3.11、IsaacSim5.1.0.0、OmniGibson3.9.2、torch2.7.0+cu128、BDDL3.7.0、MCP1.28.1。版本数字仍不足以替代本地修改后的上游源码：必须使用`upstream/`的基点与覆盖文件恢复，并按完整清单核验。

原本地热饮在RTX4090运行。master复跑使用RTX PRO 6000 Blackwell，并统一设置`NVIDIA_TF32_OVERRIDE=0`解决旋转断言误差；冻结代码和断言未放宽。复跑两组均11/11，动作/失败为107/9和93/7。无长程控制会话最初缺少code-mode-host，保留3条客户端错误，补齐后按既定协议同线程继续，未重置回合；因此这次迁移结果有额外上下文/开销差异，不能作为纯粹的长程工具因果证据。

大型USD、纹理、机器人、地图、环境目录和模型认证仍外置。野派的精确布料拓扑、原生初态、约简场景、粒子兼容层等还需按对应spec的路径/哈希取回；热饮资产清单不自动覆盖所有其他任务资产。历史code/metadata的捕获不代表全部外部资产已经闭合或每个任务已通过。

本次整理没有更改共享环境、驱动或数据。已验证的是现有环境的异机克隆迁移，以及本归档的离线代码恢复/输入准备；尚未完成从公共源全新安装全套依赖的验收。

历史在线导航还依赖外部Cartographer worker与68项库文件；`cartographer_probability_20261004_r2/`保留原构建参数、源码/库哈希与ldd记录，当前worker哈希再次匹配，二进制没有入Git。此依赖不自动套用到已记录GT-grid导航的热饮配对。
