# 热饮配对实验：master 异机复跑结果

记录时间：2026-10-27T02:51:35+08:00。

两组均在服务器 master 上完成全部 11 个高层语义目标，正式 finish、配置审计、状态日志重算和视频解码均通过。主代码和上游快照保持冻结版本。

| 组别 | 原实验动作 / 执行失败 | 本次动作 / 执行失败 | 高层目标 | 仿真步 | 回合墙钟 |
|---|---:|---:|---:|---:|---:|
| 无长程 | 106 / 11 | 107 / 9 | 11/11 | 6073 | 61.01 分钟 |
| 有长程 | 96 / 7 | 93 / 7 | 11/11 | 5158 | 46.87 分钟 |

动作数采用原分析器口径，包含 act 和 look；执行失败指这两类工具的失败结果，不包含启动阶段的客户端错误。

本次有长程相对无长程：动作数 -13.1%，执行失败次数 -22.2%，仿真步 -15.1%。导航调用 41 → 30；执行工具耗时 23.24 → 18.69 分钟。上述数字是这一对回合的描述性结果。

## 协议与输入核验

- instance301 / seed0；complete_static_shell_v2；120 动作 / 20000 仿真步；无整场墙钟截止；先无长程，后有长程。
- 请求模型 gpt-6-astra / low，保留原 provider 与严格 MCP 传输协议。传输证据记录请求身份，不能独立证明 provider 内部模型实现。
- 两组使用同一张 GPU 0：NVIDIA RTX PRO 6000 Blackwell Server Edition，UUID GPU-82875626-2b46-d877-466e-74fadb85959b。
- 冻结主代码 180 文件、上游 1393 文件、103841 个基础资产文件及 32423 个任务覆盖层文件通过 SHA-256 校验。
- 两组全部任务输入哈希与原实验一致；生成场景 SHA-256 为 1a0b0371a30a11bfa1478fadb24542e9752ce21ccc380b3d5a15e30adaeaa750。
- 两组各 78 个运行时 Python 源文件匹配冻结源码，四个共享基础技能逐文件一致；场景配置、渲染设置、contact cache 和终止协议一致。
- GPU 水系统初始化后切 CPU 刚体，运行期 contact cache 启用。
- 两组配置审计、模型调用与模拟器调用对齐、独立语义状态轨迹核验、空手结束检查和完整视频解码均通过。

## 移植与比较限制

1. 新 GPU 上，上游开启 TF32 的旋转矩阵计算超过了冻结断言容差。两组统一使用 NVIDIA_TF32_OVERRIDE=0。固定 100 个有效旋转的对照中：原 RTX4090 全部通过，新 GPU 默认 95 次失败，禁用 TF32 后全部通过，最大误差与原 GPU 相同。没有修改冻结源码或放宽断言。两次失败诊断均保留，未进入自主机器人执行。
2. 环境采用已安装环境克隆，并修正 Conda 打包缓存与实际 pip 覆盖文件的差异；完整分发版本枚举与原环境一致。该过程验证了环境迁移，尚不能表述为从公共依赖源全新安装的完整锁验收。
3. 首次无长程控制会话缺少同版本 codex-code-mode-host，产生 3 条客户端错误。补齐并校验二进制后，冻结控制器按既定恢复协议在同一线程、原场景继续；没有重置或按成绩替换回合。原始错误与恢复均留存。额外上下文和模型调用影响第一组开销及潜在行为，不能把本次差异单独归因于长程组合。原分析器的 controller_error_events 字段没有计入这类嵌套 item 错误，须结合 client_infrastructure_incident.json 阅读。
4. 仅一对非随机顺序的开发实例；有长程处理同时包含长程文档、三个计划记忆工具和 active_subgoal 字段。
5. 11/11 是本高层热饮语义协议成绩，不是官方原生 BDDL 成绩。已做 AI 最终画面检查；完整人工冗余动作与恢复区间成本复核仍未完成，未复用原实验的人工评审材料。

## 结果与证据

- [自动对照原始数据](/mnt/data/home/sunyujie/robot_manipulation/experiments/hot_drink_remote_deployment_20261026/remote_evidence/comparison_final.json)
- [成对审计](/mnt/data/home/sunyujie/robot_manipulation/experiments/hot_drink_remote_deployment_20261026/remote_evidence/pair_configuration_audit.json)、[运行与场景一致性](/mnt/data/home/sunyujie/robot_manipulation/experiments/hot_drink_remote_deployment_20261026/remote_evidence/runtime_and_scene_equivalence.json)
- [本地证据文件校验清单](/mnt/data/home/sunyujie/robot_manipulation/experiments/hot_drink_remote_deployment_20261026/replicated_results_manifest.json)：22 个关键日志、视频和页面与服务器 SHA-256 完全一致。
- [无长程五视图回放](/mnt/data/home/sunyujie/robot_manipulation/experiments/hot_drink_remote_deployment_20261026/replicated_trials/without_long_horizon/run/review_5view.html)、[有长程五视图回放](/mnt/data/home/sunyujie/robot_manipulation/experiments/hot_drink_remote_deployment_20261026/replicated_trials/with_long_horizon/run/review_5view.html)
- [无长程视频](/mnt/data/home/sunyujie/robot_manipulation/experiments/hot_drink_remote_deployment_20261026/replicated_trials/without_long_horizon/run/episode.mp4)、[有长程视频](/mnt/data/home/sunyujie/robot_manipulation/experiments/hot_drink_remote_deployment_20261026/replicated_trials/with_long_horizon/run/episode.mp4)
- [客户端基础设施事件](/mnt/data/home/sunyujie/robot_manipulation/experiments/hot_drink_remote_deployment_20261026/remote_evidence/client_infrastructure_incident.json)、[移植记录](/mnt/data/home/sunyujie/robot_manipulation/experiments/hot_drink_remote_deployment_20261026/migration_record.json)、[部署说明](/mnt/data/home/sunyujie/robot_manipulation/experiments/hot_drink_remote_deployment_20261026/README.md)

服务器目录：

```text
/data/zx/sunyujie/robot_manipulation/experiments/hot_drink_lh_repaired_pair_remote_20261026
```

连接：ssh -p 26529 zx@10.70.160.59。解释器：/data/zx/sunyujie/envs/behavior/bin/python。模型客户端的完整独立二进制包位于 /data/zx/sunyujie/bin，实验认证保留在权限受限的独立目录中。

## 最终画面

无长程：

![无长程最终四视图](/mnt/data/home/sunyujie/robot_manipulation/experiments/hot_drink_remote_deployment_20261026/remote_evidence/without_final_views.jpg)

有长程：

![有长程最终四视图](/mnt/data/home/sunyujie/robot_manipulation/experiments/hot_drink_remote_deployment_20261026/remote_evidence/with_final_views.jpg)
