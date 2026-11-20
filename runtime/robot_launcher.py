#!/usr/bin/env python3
"""便携启动器：sim / agent 共用任务、端口和结果目录，不包含模型凭据。"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import sys
from datetime import datetime
from urllib.request import ProxyHandler, build_opener
from uuid import uuid4
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
ROOT = Path(os.environ.get("MAS_RUNTIME_ROOT", str(Path.home()/".local/share/manipulation-agentic-system"))).expanduser().resolve()
CURRENT = ROOT / "launcher" / "current_run.json"

def load(path):
    return json.loads(path.read_text())

def write_atomic(path, value):
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)

def owned_simulator(state):
    try:
        command = (Path("/proc") / str(state["simulator_launcher_pid"]) / "cmdline").read_bytes().decode(errors="replace")
        return "manipulation_agent.vision_cli" in command and str(Path(state["run_dir"]) / "env") in command
    except (FileNotFoundError, KeyError, PermissionError):
        return False

def settings_from_file(task_override, instruction_override=None):
    config = load(REPO / "runtime/robot_config.json")
    if task_override:
        config["task"] = task_override
    if not isinstance(config.get("bridge_port"), int) or not 1024 <= config["bridge_port"] <= 65535:
        raise ValueError("bridge_port 必须是 1024–65535 的整数")
    if not isinstance(config.get("instance"), int) or config["instance"] <= 0:
        raise ValueError("instance 必须是正整数")
    if not isinstance(config.get("seed"), int):
        raise ValueError("seed 必须是整数")
    if not isinstance(config.get("model"), str) or not config["model"]:
        raise ValueError("model 必须是非空字符串")
    if config.get("reasoning_effort") not in {"low", "medium", "high", "xhigh"}:
        raise ValueError("不支持的 reasoning_effort")
    if config.get("control_mode", "rgb") not in {"rgb", "oracle-waypoints"}:
        raise ValueError("control_mode 必须是 rgb 或 oracle-waypoints")
    if config.get("grasping_mode", "assisted") not in {"assisted", "physical"}:
        raise ValueError("grasping_mode 必须是 assisted 或 physical")
    catalog = load(REPO / "src/manipulation_agent/tasks.json")["tasks"]
    task = config["task"]
    instruction = instruction_override or config.get("instruction") or catalog.get(task, {}).get("instruction")
    if not isinstance(instruction, str) or not instruction.strip():
        raise ValueError("该任务没有内置公开指令，请通过 --instruction 显式传入：" + str(task))
    return config, instruction

def command_for(mode, state, python):
    config = state["settings"]
    run = Path(state["run_dir"])
    bridge = "http://127.0.0.1:" + str(config["bridge_port"])
    oracle = config.get("control_mode", "rgb") == "oracle-waypoints"
    profile = "oracle_waypoints" if oracle else "skills"
    if mode == "sim":
        command = [python, "-u", "-m", "manipulation_agent.vision_cli",
                "--backend", "omnigibson", "--policy", "serve",
                "--agent-profile", profile, "--goal-skill", "none" if oracle else "hierarchical",
                "--task", config["task"],
                "--instruction", state["instruction"],
                "--instance", str(config["instance"]), "--seed", str(config["seed"]),
                "--record-video", "--unbounded-episode",
                "--output", str(run / "env"), "--port", str(config["bridge_port"])]
        if oracle:
            command += ["--grasping-mode", config.get("grasping_mode", "assisted"),
                        "--waypoint-max-steps", str(config.get("waypoint_max_steps", 360)),
                        "--ik-backend", "omnigibson", "--wrist-cameras"]
        else:
            command += ["--require-task-notes"]
        return command
    command = [python, "-u", str(REPO / "scripts/run_codex_controller.py"),
            "--model", config["model"], "--reasoning-effort", config["reasoning_effort"],
            "--agent-profile", profile, "--goal-skill", "none" if oracle else "hierarchical",
            "--instruction", state["instruction"], "--mcp-command", python,
            "--mcp-args-json", json.dumps(["-m", "manipulation_agent.mcp_server", "--bridge", bridge]),
            "--timeout", "0", "--output", str(run / "controller")]
    if oracle:
        command += ["--isolate-client-storage", "--blocking-tool-yield-ms", "2147483647",
                    "--auto-compact-token-limit", "48000", "--rate-limit-resumes", "0"]
    return command

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["sim", "agent"], help="sim 启动仿真；agent 连接最近一次仿真")
    parser.add_argument("--task", help="仅 sim 可用；覆盖配置文件中的任务名称")
    parser.add_argument("--instruction", help="仅 sim 可用；没有内置指令时显式提供任务或工程子目标")
    parser.add_argument("--control-mode", choices=["rgb", "oracle-waypoints"],
                        help="sim 的控制模式；agent 自动沿用")
    parser.add_argument("--grasping-mode", choices=["assisted", "physical"],
                        help="Oracle 模式的原生抓取设置")
    parser.add_argument("--gpu", type=int, help="sim 使用的非负 GPU 索引")
    parser.add_argument("--dry-run", action="store_true", help="只打印完整命令，不创建运行目录或启动进程")
    args = parser.parse_args()
    if args.mode == "agent" and any(value is not None for value in (args.task, args.instruction, args.control_mode, args.grasping_mode, args.gpu)):
        parser.error("任务由 sim 端确定，agent 自动沿用")
    python = sys.executable
    prefix = []
    if os.environ.get("MAS_USE_PROXY") == "1":
        proxy = shutil.which("proxy")
        if proxy is None:
            parser.error("MAS_USE_PROXY=1 but proxy is not installed")
        prefix = [proxy]
    if args.gpu is not None and args.gpu < 0:
        parser.error("GPU index must be nonnegative")

    lock = None
    if args.mode == "sim":
        config, instruction = settings_from_file(args.task, args.instruction)
        if args.control_mode is not None:
            config["control_mode"] = args.control_mode
        if args.grasping_mode is not None:
            config["grasping_mode"] = args.grasping_mode
        if args.gpu is not None:
            config["gpu"] = args.gpu
        if args.dry_run:
            run = ROOT / "runs" / ("preview_" + config["task"])
        else:
            CURRENT.parent.mkdir(parents=True, exist_ok=True)
            lock = (CURRENT.parent / "startup.lock").open("a")
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if CURRENT.exists() and owned_simulator(load(CURRENT)):
                parser.error("最近一次仿真仍在运行；请连接 agent，或先结束该仿真")
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", config["bridge_port"]))
            stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d_%H%M%S")
            run = ROOT / "runs" / ("learning_" + config["task"] + "_" + stamp + "_" + uuid4().hex[:8])
        state = {"settings": config, "instruction": instruction, "run_dir": str(run),
                 "simulator_launcher_pid": os.getpid()}
    else:
        if CURRENT.exists():
            state = load(CURRENT)
        elif args.dry_run:
            config, instruction = settings_from_file(None)
            state = {"settings": config, "instruction": instruction,
                     "run_dir": str(ROOT / "runs" / ("preview_" + config["task"]))}
        else:
            parser.error("尚无仿真运行记录；先在另一个终端执行 sim")
        run = Path(state["run_dir"])
        if not args.dry_run:
            if not owned_simulator(state):
                parser.error("最近一次仿真进程已结束；请重新启动 sim")
            if (run / "controller").exists():
                parser.error("本次 controller 目录已存在，保留已有证据；请启动新的 sim")
            bridge = "http://127.0.0.1:" + str(state["settings"]["bridge_port"])
            try:
                with build_opener(ProxyHandler({})).open(bridge + "/healthz", timeout=3) as response:
                    health = json.load(response)
                if not health.get("ready") or health.get("closed"):
                    parser.error("仿真未就绪或已关闭；等待 MAS_BRIDGE_READY")
            except OSError:
                parser.error("仿真服务尚未就绪；看到 MAS_BRIDGE_READY 后再执行 agent")

    command = [*prefix, *command_for(args.mode, state, python)]
    print("任务：" + state["settings"]["task"], flush=True)
    print("结果目录：" + str(run), flush=True)
    print("完整命令：" + shlex.join(command), flush=True)
    if args.dry_run:
        return 0
    if args.mode == "sim":
        run.mkdir(parents=True, exist_ok=False)
        write_atomic(run / "launcher.json", state)
        write_atomic(CURRENT, state)
        print("等待 MAS_BRIDGE_READY；之后在另一个终端执行 robot.sh agent。", flush=True)
    log = run / ("simulator.log" if args.mode == "sim" else "controller.log")
    print("终端日志：" + str(log), flush=True)
    env = dict(os.environ, MAS_LAUNCH_LOG=str(log))
    if state["settings"].get("control_mode") == "oracle-waypoints":
        env.setdefault("MAS_MANIP_ENGINE", "model_program_v1")
        env.setdefault("MAS_MANIP_TIMING", "model_authored_v1")
        env.setdefault("MAS_NATIVE_TRANSPORT", "native_joint_v1")
        env.setdefault("MAS_NAV_SMOOTH", "1")
        env["MAS_CLIENT_STATE_ROOT"] = str(run / "client_state")
    if "gpu" in state["settings"]:
        env["OMNIGIBSON_GPU_ID"] = str(state["settings"]["gpu"])
    os.chdir(REPO)
    # 命令按 argv 传递，不拼接用户输入；tee 同时保留终端输出和日志。
    os.execvpe("bash", ["bash", "-c", 'set -o pipefail; "$@" 2>&1 | tee -a "$MAS_LAUNCH_LOG"',
                       "robot-launch", *command], env)

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as exc:
        print("启动失败：" + str(exc), file=sys.stderr)
        raise SystemExit(2)
