# Manipulation Agentic System · RGB v0.2

独立维护的 OmniGibson manipulation agent。**模型通过 RGB 完成观察、搜索、识别、计划、记忆、动作选择、恢复和核查；仅底层动作执行由仿真器理想化接口代替。**

旧版 v0.1 的 oracle-state 成功记录属于历史 harness 集成基线，不能作为当前 RGB 版本的任务成绩。当前规则见 [RGB协议](docs/rgb_protocol.md)，历史记录见 [v0.1验证](docs/validation.md)。

## 文件在哪里

```text
skills/                              # Agent 的工作流技能文档
  visual-manipulation/SKILL.md        # 观察—计划—行动—视觉核查—结束
  visual-manipulation/references/     # 证据规则
  visual-exploration/SKILL.md         # 搜索、转向、视觉路标、返回
  pick-and-place/SKILL.md             # 容器准备、抓取、搬运、放置
  failure-recovery/SKILL.md           # 新观测、记忆、修订与有界恢复
src/manipulation_agent/
  tools/                             # 9 个实际工具的schema与实现
    base.py                          # Tool定义与注册中心
    observation.py                   # observe、look
    action.py                        # act
    planning.py                      # update_plan
    memory.py                        # remember、recall
    skills.py                        # list_skills、read_skill
    session.py                       # finish
  observations/boundary.py           # RGB公开观测白名单
  executors/primitives.py            # 10个机器人动作原语
  executors/omnigibson_rgb.py         # RGB采集、像素射线、私有小脑执行
  skill_runtime.py                   # skill快照、SHA、资源白名单
  vision_harness.py                  # 校验、版本、预算、闭环记录
  vision_policy.py                   # RGB模型提示与Responses图像循环
  vision_cli.py                      # 当前默认入口
  mcp_server.py                      # MCP文本＋真实图像内容
  bridge.py                          # HTTP排队与单线程仿真所有者
  records.py                         # run/events/源码快照/HTML
  audit.py                           # 控制器与仿真记录交叉检查
  replay.py / replay_assets/          # 逐步观测、动作与公开决策记录的回放
```

**Skill文档、tool接口和动作primitive是三层不同概念。** Skill告诉模型如何组织工具完成任务；tool是可调用能力；primitive负责一次底层动作。技能库是人工编写并冻结到每轮运行的工作流，尚未实现自动技能学习或演化。

## 当前公开输入与动作

`observe` 返回 head、left_wrist、right_wrist 三路 512×512 RGB，附带 image_ref、view、尺寸、图像SHA和revision。MCP返回真实ImageContent，模型不用读取文件路径。没有对象列表、真实ID/名称、深度、距离、分割图、地图、状态谓词、持物真值或评分。

`act(primitive, target, revision)` 的 target 为 `{"image_ref":"最新图像引用","point":[0.5,0.5]}`。模型必须自己从RGB选点；x左0右1，y上0下1。release/wait使用null。`look(yaw_degrees, revision)` 原地转向，范围±90度，正值左转。所有执行尝试返回新RGB；旧图像引用失效。

10个primitives：navigate_to、grasp、place_inside、place_on_top、open、close、toggle_on、toggle_off、release、wait。

理想执行器内部使用相机标定及射线首个碰撞，把模型选择的像素转成低层目标；不会按对象名字寻找目标、生成GT候选或返回对象标签。导航仍为可通行地图上的终点移动，操作仍采用官方symbolic primitive/容器体积采样。其执行耗时不能解释为真实物理控制性能。

## 启动与测试

Python 3.11+。无仿真依赖的契约测试：

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

实际RGB MCP图像传输测试需要 `mcp==1.28.1`，只使用CPU fixture：

```bash
PYTHONPATH=src python scripts/probe_rgb_mcp.py runs/rgb-mcp-probe
```

当前仿真适配固定 OG3.9.2 / Isaac5.1 / Torch2.7.0+cu128 / BDDL3.7.0，BEHAVIOR资产3.9.0、robot资产3.8.2。使用项目独立环境，不改旧导航环境。设置官方数据与缓存路径后：

```bash
PYTHONPATH=src python -m manipulation_agent.vision_cli \
  --backend omnigibson --policy serve --task turning_on_radio \
  --instance 301 --output runs/rgb-radio --port 29440
```

S134使用 `scripts/run_s134.sh`，其默认入口已经切换为vision_cli。启动GPU任务前检查资源，并使用唯一unit/run目录；记录机器、解释器、GPU UUID、PID/unit和版本。

模型通过 `python -m manipulation_agent.mcp_server --bridge http://127.0.0.1:29440` 接入。`scripts/run_codex_controller.py` 使用既有登录客户端，禁用shell、任意文件查看、web和其他agent，只开放本项目九个工具。首次读取工作流skills后，观察RGB并自主执行。

也实现了 `--policy responses` 的RGB循环，配置仅从私有环境变量读取：LLM_MODEL、LLM_BASE_URL、LLM_API_KEY。不要把凭据写入仓库或日志。提供商路径是否完成真实模型验证，以本轮记录为准。

## 评测和记录

finish只返回关闭状态与模型自己的声明；独立BDDL/TaskMetric结果不回流给活动模型。工具完成、模型声称成功与实际任务成功分开记录。模型的笔记与计划都是判断，不是真值。原始图像、公开工具回复和私有执行器诊断分别记录；私有诊断不会经过MCP发送给模型。

每轮保存skill_snapshot/manifest、source_snapshot、图像SHA、模型调用、run.json、events.jsonl、captures.jsonl和HTML。已知低层限制包含小容器采样和大碗辅助抓取时的物理稳定性；不能把理想小脑假设写成永远成功。当前也不是官方物理控制排行榜提交。

[RGB当前页面](http://10.76.5.241:8765/rgb_system.html) · [历史基线与来源仓库分析](http://10.76.5.241:8765/manipulation_system.html)

## 真实任务 Replay

每轮RGB实验结束自动生成 `replay.html` 与 `replay.json`。关联模型日志后重建，可验证模型实际收到的ImageContent与落盘图像哈希、工具参数/结果、源码及正式结束：

```bash
PYTHONPATH=src python -m manipulation_agent.replay \
  --run-dir runs/rgb-radio --controller-dir runs/rgb-radio-controller
```

回放包含完整工具时间线、步进/自动播放、三相机前后RGB、模型传入像素叠加、执行错误、当时已记录的计划/记忆及技能读取。决策摘要只来自显式工具参数，不提供隐藏推理或事后编造动机。没有新观测的步骤明确沿用前图；构造器内部未返回模型的初始画面不混入模型视图；未录制的运动中间帧不插值。最终BDDL评分单列为实验后审阅信息。

2026-09-30：`turning_on_radio / 301 / seed 0`，真实模型gpt-6-astra、真实OmniGibson相机RGB、理想执行器，BDDL与TaskMetric成功。3机器人动作、16工具调用、180 env.step；18个实际图像内容块/15张不同图像，控制器与仿真轨迹及哈希对齐。实验源码 `e48a7ac`，后续离线审计与replay改动不改变原运行版本。这是1个实例的通过记录，不代表100任务成绩或官方物理控制排行榜结果。
