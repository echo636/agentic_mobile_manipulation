# Historical implementation before fixed four-camera observations

These records describe the earlier three-camera/six-tool protocol. They are not current defaults.

# Manipulation Agentic System · RGB + callable Skills

当前目标是先打通最小闭环：**任务指令 → RGB观察 → 模型选择动作 → OmniGibson理想执行 → 新RGB → 继续或结束**。

默认开放6个MCP工具：`observe`、`look`、`act`、`finish`、`list_skills`、`read_skill`。加载冻结的四个工作流Skill，不实例化显式plan/memory，不要求阶段性计划。模型保留正常对话上下文。旧四工具模式以`--agent-profile minimal`、旧九工具模式以`--agent-profile workflow`保留供历史复现。

[当前原始消息与视频网页](http://10.76.5.241:8765/skills_replay.html)

Replay逐字展示模型实际的公开assistant输出与原始MCP消息，不强制中文decision摘要，不将公开说明称为隐藏思维链。无公开文本的步骤明确标为空缺，不事后补写。

## 模型输入与小脑

模型只接收任务指令、头部/左右腕部512×512 RGB、图像引用/版本，以及操作完成或失败反馈。没有真实对象列表、ID、地图、距离、谓词、持物真值或评估分数。

`act`的目标为最新RGB中的归一化像素：

```json
{"primitive":"grasp","target":{"image_ref":"最新图像引用","point":[0.5,0.6]},"revision":1}
```

V3执行器内部读取模型所选像素的私有线性深度，核对同一条射线上排除机器人碰撞代理后的命中点；不搜索其他像素或任务对象，不启用实例分割。使用官方symbolic动作接口及逐控制步理想运动学导航。10个动作：navigate_to、grasp、place_inside、place_on_top、open、close、toggle_on、toggle_off、release、wait。搜索使用look（±90°，正值左转）。目标识别与选点由模型自己完成。

当前是三路相机RGB，不是环视RGB。look转动底盘后同步返回新图，没有自动360°扫描、全景拼接或异步观测任务。已实现观察—动作—反馈—继续/结束闭环；尚无规划图中的显式任务步骤Stack、独立完成判断节点。对话上下文保留历史，但不等同于独立plan/memory。

## 连续执行视频

真实OmniGibson运行默认启用录像（`--no-record-video`可关闭；mock默认不录制），在每个env.step后录制RGB，生成`episode.mp4`、`video.json`和`video_frames.jsonl`。四格画面包含头部、左右腕部相机，以及仅供研究者查看的第三人称；第三人称没有进入MCP图像注册表。

视频按控制步仿真时间播放，省略模型等待期间的暂停；额外观测边界记录一帧。底盘按≤0.5m/s和≤60°/s逐步执行，理想关节位置保持避免姿态漂移；不是物理导航控制。理想抓放的状态瞬变如实保留，无运动插帧。覆盖范围为全部env.step，不含内部物理子步或放置采样器的候选搜索过程。Replay页面支持MP4播放/慢放/下载及动作步骤同步。`video.json`检查连续控制步覆盖率，视频成功与任务成功独立记录。

另有`explained.mp4`同步版：原始视频帧全部按顺序保留，额外标注停顿显示原始LLM公开文本、工具调用与返回。`explained_video.json`逐区间记录来源帧与步骤。模型等待被压缩，停顿为阅读时间。网页支持原始/同步版切换与HTTP分段读取。

## 运行

固定OG3.9.2、Isaac5.1、Torch2.7+cu128、BDDL3.7和对应BEHAVIOR资产，配置见项目批次记录；不修改旧导航环境。

```bash
PYTHONPATH=src python -m manipulation_agent.vision_cli \
  --backend omnigibson --policy serve --agent-profile skills --record-video \
  --task turning_on_radio --instance 301 --output runs/minimal-radio --port 29441
```

模型接入`python -m manipulation_agent.mcp_server --bridge http://127.0.0.1:29441`。`scripts/run_codex_controller.py`默认也使用skills，禁用shell、任意文件、web等外部工具，沿用既有客户端登录，不复制凭据。

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python scripts/probe_rgb_mcp.py runs/mcp-probe
PYTHONPATH=src python scripts/probe_video.py runs/encoder-probe
PYTHONPATH=src python -m manipulation_agent.replay \
  --run-dir runs/minimal-radio --controller-dir runs/minimal-radio-controller
```

## 代码位置与证据

- `tools/observation.py`、`tools/action.py`、`tools/session.py`：观察/执行/结束工具，另有`tools/skills.py`的两个skill工具，`tools/__init__.py`控制profile白名单。
- `vision_policy.py`、`vision_harness.py`：直接RGB闭环、版本/预算、动作反馈与正式结束。
- `executors/omnigibson_rgb.py`：机器人RGB、私有像素执行、离线第三人称与逐控制步录制。
- `video.py`：流式H.264编码、帧时间线和覆盖验证。
- `replay.py`、`replay_assets/`：视频及逐步回放，skills/minimal模式不显示plan/memory面板。
- `audit.py`：关联真实模型和仿真工具轨迹、图像字节与源码版本。

实验保存源码/依赖/资产/任务实例版本、主机/解释器/GPU/PID/unit、公开与私有记录。最终BDDL和TaskMetric独立评分，不返回活动模型。任务成绩以真实run records为准；不把CPU mock、编码成功或旧oracle成绩当成当前任务成功。当前是理想执行器研究协议，不是官方物理控制排行榜提交。

[系统与实验网页](http://10.76.5.241:8765/rgb_system.html)

## 历史四工具验证结果（2026-09-30）

真实gpt-6-astra + OmniGibson + minimal四工具：

|任务/实例|独立结果|动作/调用|连续视频|
|---|---|---|---|
|turning_on_radio / 301 / seed0|BDDL和TaskMetric通过|3 / 5|185帧，180 env.step，6.17秒|
|picking_up_trash / 301 / seed0|3个罐子全部入桶，BDDL和TaskMetric通过|25 / 27|1187帧，1160 env.step，39.57秒|

Radio实际源码c77e2f5、trash实际源码3931d44；后续默认配置/文档更新不改写原实验版本。两轮均审计了模型实际图像字节和精确工具调用，无plan/memory/skill调用。视频录制不包含模型等待与内部采样候选搜索；任务覆盖仅这两个实例。

[完整视频页面](http://10.76.5.241:8765/minimal_loop.html) · [抓放任务Replay](http://10.76.5.241:8765/manipulation_runs/mas_minimal_video_trash_r1/replay.html)

当前逐步底盘运动与Skill轮次的成绩独立记录在`operations/skills_replay_20260930`。r1姿态漂移失败；r2碰撞代理错误拒绝可见目标，最终Q=1/3；r3原生渲染崩溃，没有最终评分。失败记录均保留。

r4（实际源码`1005384`）使用V3私有深度核对和逐步理想底盘运动，真实gpt-6-astra完成`picking_up_trash/public_test/301/seed0`：三个目标均通过独立BDDL/TaskMetric评估，Q=1，25动作/32调用/1878控制步，零执行器报错。模型实际读取3种Skill及一份参考文档，接收78张RGB图像；源码、调用和图像字节审计通过。原始视频1905帧、30FPS、63.5秒。仅此实例通过，不代表整个challenge验证。

[当前Skill闭环成功回放](http://10.76.5.241:8765/manipulation_runs/mas_skills_video_trash_r4/replay.html)

该轮原始消息同步版为1920×1080、30FPS、212.5秒；保留全部原始帧并加入明确标注的阅读停顿。原始消息、精确工具返回、Skill调用和选点证据可逐步查看。全部16项视频/运动/像素路由审计通过；任务成功与展示验证分别记录。
