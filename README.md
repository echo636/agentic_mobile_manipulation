# Agentic Mobile Manipulation

在 OmniGibson 上研究 **RGB 观测 → LLM 选择动作和对象 → 理想低层执行 → 新观测** 的闭环。

维护仓库：[echo636/agentic_mobile_manipulation](https://github.com/echo636/agentic_mobile_manipulation)。独立代码库，保留本项目开发历史；不修改来源仓库或旧导航环境。

## 当前观测：四个固定相机

- 四路 512×512 RGB：**front（前）、back（后）、left（左）、right（右）**，相对机器人朝向。
- 相机刚性跟随机器人。一次 render barrier 后读取四个 render product，使用共同 capture_id、captured_at 和 sim_step。采集不转动机器人、不调用 env.step。
- 不使用腕部相机；仿真器默认的头部/腕部传感器由四相机研究配置替代。相机位于初始机器人最高点上方 5cm 的固定底盘相对高度，安装半径 35cm，水平视场 90°，下倾 20°。这是明确记录的仿真配置，不宣称真实硬件标定。
- 大脑只看 RGB 和采集元信息；深度、相机外参、对象身份与任务真值只在理想执行器或离线评估中使用。

## 异步观测工具

```text
start_observation({}) → job_id, status=planned（立即返回）
get_observation({job_id}) → planned/running 或 passed + 四张 RGB
cancel_observation({job_id}) → 请求取消尚未完成的任务
```

任务在仿真主线程的后台调度点采集，网络请求和 LLM 推理可以继续。没有并发线程操作 OmniGibson。采集本身是一个不可分割的只读操作；已经开始的 render 不会被中途打断。取消与采集竞争时，以最终状态为准。

每个结果带 `stale`：新动作或新采集后，旧结果仍可查看，但不能作为最新动作目标。当前四个方向中的任何一个 RGB 像素均可供 `act` 选取，无需先转身获取后方图片。

默认 9 个 MCP 工具：`start_observation`、`get_observation`、`cancel_observation`、`observe`、`look`、`act`、`finish`、`list_skills`、`read_skill`。`observe` 是同步兼容入口；`look` 是明确的底盘转向动作，不是环视采集。动作后也返回同一状态的四路 RGB。

四个可读取 Skill：visual-manipulation、visual-exploration、pick-and-place、failure-recovery。暂不引入显式 plan/Stack/memory。Skill 是工作流指导，动作仍由 LLM 通过工具调用。

## 安装与运行

Python >=3.11，CPU 核心不依赖仿真器：

```bash
python -m pip install -e '.[mcp]'
PYTHONPATH=src python -m unittest discover -s tests -v
```

真实执行已使用独立的 OmniGibson 3.9.2 / Isaac Sim 5.1.0 / torch 2.7.0+cu128 / BDDL 3.7.0 环境；BEHAVIOR 资产需要自行按官方许可安装，不包含在 Git 仓库中。

```bash
PYTHONPATH=src python -m manipulation_agent.vision_cli \
  --backend omnigibson --policy serve --agent-profile skills \
  --task turning_on_radio --instance 301 --output runs/radio --port 29440
PYTHONPATH=src python -m manipulation_agent.mcp_server --bridge http://127.0.0.1:29440
```

[MCP 异步采集探针](scripts/probe_async_observation.py)对运行中的 bridge 验证接口；它是脚本测试，不等于 LLM 完成任务。

## 执行和实验记录

目标由模型在最新 RGB 中选点；执行器使用私有几何完成导航及官方 symbolic/volume 操作。底盘逐控制步理想运动，抓放可能瞬变；不是物理控制排行榜提交。最终 BDDL/TaskMetric 在结束后独立评分，不返回活动模型。

原始视频、图像、完整公开模型消息、工具轨迹、Skill 快照、版本、主机/解释器/GPU/PID 和失败记录保存在各 run 中。Replay 不补写隐藏思考或不存在的机械臂运动。原始数据和资产不提交到 Git。

- [观测与工具协议](docs/rgb_protocol.md)
- [四相机异步设计与验证](docs/async_observation.md)
- [此前三相机实验记录](docs/history_before_four_camera.md)：历史成功结果不自动代表新相机配置通过。
