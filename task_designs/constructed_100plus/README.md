# 100+ 长程任务的构建入口

这里的四份 JSON 是可执行的 **场景构建配方**，不是已经通过完整测评的冻结任务。
它们通过原生资产导入、联合初态摆放、独立评分和参考动作程序接入当前系统。
源码在 `feat/long-horizon-tasks`；本批实验记录为共享根目录下
`operations/long_horizon_build_20261020/`，最新分层状态见该目录的 `validation.json`。

| 配方 | 实际工作 | 条件有效动作数 |
| --- | --- | ---: |
| `garden_treatment_and_distribution_100_v1.json` | 20 盆处理、两张绿台各 10 盆、4 盆对照保护 | 100 |
| `hotel_shoe_care_and_pairing_100_v1.json` | 20 只鞋去尘、10 个鞋位各两只且相邻 | 100 |
| `bedroom_vacuum_reset_close_102_v1.json` | 两卧室各 12 件腾空、吸尘、归位、最后关四灯 | 102 |
| `garage_cut_sort_store_113_v1.json` | 扫地、16 根原木切成 32 半、逐件收纳 | 113 |

数字是逐件搬运协议下的设计账本，尚非实测轨迹或最短解。导航、等待、重试和工具交接另计。
场景使用公开的蓝色来源台、黄色处理/暂存台、绿色终点台、红色对照台、灰色工具台；
同款对象允许等价匹配。鞋履不使用未提供的主人标签；木料按原生父子身份和模型验证产物，
禁止预先放入半木充数。任务成功取决于物理状态、公开阶段历史和搬运约束，不取决于调用数。

2026-10-20 实际构建记录：花园 r5、酒店 r6、卧室 r5、车库 r5 均通过真实场景初态断言、
相机预热后的复查、台面与背景几何筛查、机器人出生位置检查，已输出冻结场景和任务。
这四种检查不证明通路可达或完整任务可解。卧室首次参考在抓取后导航到暂存台失败，
车库首次参考在导航到扫帚时耗尽搜索预算；对应状态、轨迹和诊断保存在该批次，
不能把这两次参考算作通过。各次重试及其余任务的实时状态以批次 `validation.json` 为准。

## 构建与运行

在已配置项目 OmniGibson 环境的机器执行。每次使用新的输出目录，保留失败记录；
正式远端实验通过共享 GPU 租约和资源准入脚本调度，不能直接争抢现有实验资源。

```sh
python -B scripts/build_custom_task.py \
  --spec task_designs/constructed_100plus/garden_treatment_and_distribution_100_v1.json \
  --output /absolute/new/build-output
```

构建检查原生模板哈希、角色绑定、初态断言和相机预热后的初态，拒绝开局已成功的任务。
输出包括 `initial_scene.json`、带场景哈希的 `task.json`、初始观察、角色与全场景几何、
`validation.json` 和进程退出证据。初态 setter 只用于造场景；参考流程不使用状态 setter。

构建通过后，从生成的冻结任务启动新进程，不能把构建进程里的中途状态作为完整验收：

```sh
python -B scripts/validate_custom_task_physics.py \
  --spec /absolute/build-output/task.json --order forward \
  --output /absolute/new/reference-forward
```

同样执行 `--order reverse`，以及三个单独的 `--negative-case`：
`skip_work`、`skip_stage`、`missing_delivery`。参考流程使用现有 checked executor 和私有角色绑定，
属于物理可行性实验，不是 RGB 自主模型成功。

## 放行条件

依次保留静态契约、真实构建、几何/通路检查、两次新进程恢复及完整参考、三个物理负例、
RGB/MCP 目标可见性与接口检查、模型评测的独立记录。`OnTop` 初态通过不能代替通路可达性。
当前所有配方仍为 `candidate_physics_unvalidated`、`benchmark_ready=false`。
新配方预留 600 次模型 act/look 尝试，参考程序上限为 1000 次，二者不是有效动作数。
`prepare_custom_evaluation.py` 只在 release 证据通过后生成测评清单，并从冻结配方读取模型预算；
不同预算或不同 schema 的任务需分批，旧任务仍默认 200 次。此入口不会启动模型实验。

其余六个多样化设计及其资产、拆装、加热或材料转移门槛见
[设计说明](../../docs/long_horizon_diverse_designs.md)。它们没有因为这里新增了四份配方而自动变成可用任务。
