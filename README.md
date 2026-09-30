# Manipulation Agentic System · Minimal RGB loop

当前目标是先打通最小闭环：**任务指令 → RGB观察 → 模型选择动作 → OmniGibson理想执行 → 新RGB → 继续或结束**。

默认仅开放4个MCP工具：`observe`、`look`、`act`、`finish`。不实例化显式plan/memory，不加载workflow skills，也不要求阶段性计划。模型保留正常对话上下文；这不是跨任务记忆模块。旧9工具工作流以`--agent-profile workflow`保留供历史复现，见[历史工作流说明](docs/workflow_quickstart.md)。

## 模型输入与小脑

模型只接收任务指令、头部/左右腕部512×512 RGB、图像引用/版本，以及操作完成或失败反馈。没有真实对象列表、ID、地图、距离、谓词、持物真值或评估分数。

`act`的目标为最新RGB中的归一化像素：

```json
{"primitive":"grasp","target":{"image_ref":"最新图像引用","point":[0.5,0.6]},"revision":1}
```

执行器内部将所选像素通过相机射线路由到底层目标，使用官方symbolic动作接口及理想导航。10个动作：navigate_to、grasp、place_inside、place_on_top、open、close、toggle_on、toggle_off、release、wait。搜索使用look（±90°，正值左转）。目标识别与选点由模型自己完成。

## 连续执行视频

使用`--record-video`，在每个env.step后录制RGB，生成`episode.mp4`、`video.json`和`video_frames.jsonl`。四格画面包含头部、左右腕部相机，以及仅供研究者查看的第三人称；第三人称没有进入MCP图像注册表。

视频按控制步仿真时间播放，省略模型等待期间的暂停；额外观测边界记录一帧。理想执行器本身的位姿/状态瞬时跳变如实保留，无运动插帧。覆盖范围为全部env.step，不含内部物理子步或放置采样器的候选搜索过程。Replay页面支持MP4播放/慢放/下载及动作步骤同步。`video.json`检查连续控制步覆盖率，视频成功与任务成功独立记录。

## 运行

固定OG3.9.2、Isaac5.1、Torch2.7+cu128、BDDL3.7和对应BEHAVIOR资产，配置见项目批次记录；不修改旧导航环境。

```bash
PYTHONPATH=src python -m manipulation_agent.vision_cli \
  --backend omnigibson --policy serve --agent-profile minimal --record-video \
  --task turning_on_radio --instance 301 --output runs/minimal-radio --port 29441
```

模型接入`python -m manipulation_agent.mcp_server --bridge http://127.0.0.1:29441`。`scripts/run_codex_controller.py`默认也使用minimal，禁用shell、任意文件、web等外部工具，沿用既有客户端登录，不复制凭据。

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python scripts/probe_rgb_mcp.py runs/mcp-probe
PYTHONPATH=src python scripts/probe_video.py runs/encoder-probe
PYTHONPATH=src python -m manipulation_agent.replay \
  --run-dir runs/minimal-radio --controller-dir runs/minimal-radio-controller
```

## 代码位置与证据

- `tools/observation.py`、`tools/action.py`、`tools/session.py`：四个当前工具，`tools/__init__.py`控制profile白名单。
- `vision_policy.py`、`vision_harness.py`：直接RGB闭环、版本/预算、动作反馈与正式结束。
- `executors/omnigibson_rgb.py`：机器人RGB、私有射线执行、离线第三人称与逐控制步录制。
- `video.py`：流式H.264编码、帧时间线和覆盖验证。
- `replay.py`、`replay_assets/`：视频及逐步回放，minimal模式不显示plan/memory面板。
- `audit.py`：关联真实模型和仿真工具轨迹、图像字节与源码版本。

实验保存源码/依赖/资产/任务实例版本、主机/解释器/GPU/PID/unit、公开与私有记录。最终BDDL和TaskMetric独立评分，不返回活动模型。任务成绩以真实run records为准；不把CPU mock、编码成功或旧oracle成绩当成当前任务成功。当前是理想执行器研究协议，不是官方物理控制排行榜提交。

[系统与实验网页](http://10.76.5.241:8765/rgb_system.html)
