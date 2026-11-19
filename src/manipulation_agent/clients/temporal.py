"""Run an unbounded serial or spatiotemporal policy against a live robot MCP session."""

import argparse
import asyncio
from collections import Counter
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from ..agent.temporal_execution import DispatchController
from ..agent.temporal_memory import Camera
from ..agent.temporal_models import ModelRole
from ..records import now, write_json
from ..runtime.deadline import validate_execution_clock
from .planner import CodexPlannerRequest


async def episode(session, controller, arm_clock):
    await session.initialize()
    specs = {tool.name: tool.inputSchema for tool in (await session.list_tools()).tools}
    for name in ("act", "look", "initialize", "finish"):
        if name not in specs:
            raise ValueError("Missing motor tool " + name)
    if "intent" in specs["act"]["properties"]:
        raise ValueError("The dispatch controller requires the base motor protocol")
    controller.catalog = tuple(specs["act"]["properties"]["primitive"]["enum"])
    arm_clock()
    camera = Camera.read(await session.call_tool("initialize", {}))
    controller.observe(camera)
    controller.log(
        "initial_rgb",
        revision=camera.revision,
        frames={k: v["sha256"] for k, v in camera.frames.items()},
    )
    while True:
        name, args = await asyncio.to_thread(controller.next)
        result = await session.call_tool(name, args)
        if name == "finish":
            closed = json.loads(result.content[0].text).get("closed") is True
            controller.log(
                "finish", outcome=args["outcome"], reason=args["reason"], closed=closed
            )
            if not closed:
                raise RuntimeError("Motor endpoint did not acknowledge closure")
            return args["outcome"]
        try:
            camera = Camera.read(result)
            controller.observe(camera)
        except (ValueError, KeyError, IndexError):
            # Read the current snapshot once. Never reissue an ambiguous motor command.
            controller.log("readback_retry", revision=controller.camera.revision)
            controller.observe(Camera.read(await session.call_tool("initialize", {})))


def run(args):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    if args.timeout or args.deadline_unix is not None:
        raise ValueError(
            "This policy runner requires an unbounded episode (--timeout 0)"
        )
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    counter = Counter()
    meta = {
        "started_at": now(),
        "controller": "spatiotemporal_dispatch",
        "policy": args.dispatch_policy,
        "planner_model": args.planner_model,
        "planner_effort": args.planner_reasoning_effort,
        "fast_model": args.model,
        "fast_effort": None if args.fast_vision_port else args.reasoning_effort,
        "fast_backend": "local_qwen" if args.fast_vision_port else "codex",
        "host": os.uname().nodename,
        "pid": os.getpid(),
        "interpreter": sys.executable,
        "status": "running",
        "task_success": None,
    }
    with (output / "dispatch.jsonl").open("x", buffering=1) as ledger:

        def log(kind, **fields):
            if kind == "model_call":
                counter[fields["role"]] += 1
            ledger.write(
                json.dumps(dict(kind=kind, at=now(), **fields), ensure_ascii=False)
                + "\n"
            )

        def arm():
            started = time.time()
            if args.execution_clock_command_json:
                cmd = json.loads(args.execution_clock_command_json)
                if (
                    not isinstance(cmd, list)
                    or not cmd
                    or not all(isinstance(s, str) and s for s in cmd)
                ):
                    raise ValueError("Invalid remote clock command")
                raw = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=30, check=True
                )
                clock = validate_execution_clock(json.loads(raw.stdout))
                if clock["budget_policy"] != "unbounded":
                    raise ValueError("Unexpected episode budget policy")
                started = clock["execution_started_at_unix"]
            else:
                clock = {
                    "budget_policy": "unbounded",
                    "execution_started_at_unix": started,
                }
            meta.update(execution_clock=clock, execution_started_at_unix=started)
            write_json(output / "controller.json", meta)

        def native(role, model, effort):
            req = CodexPlannerRequest(
                output=output / role,
                provider=args.model_provider_profile,
                gateway=args.model_provider_gateway,
            )
            return ModelRole(req, model, effort)

        from .local_vision import LocalVisualRole

        fast = (
            LocalVisualRole(args.fast_vision_port)
            if args.fast_vision_port
            else native("fast", args.model, args.reasoning_effort)
        )
        agent = DispatchController(
            args.instruction,
            (),
            native("slow", args.planner_model, args.planner_reasoning_effort),
            fast,
            serial=args.dispatch_policy == "serial",
            log=log,
        )

        async def connect():
            params = StdioServerParameters(
                command=args.mcp_command, args=json.loads(args.mcp_args_json)
            )
            with (output / "transport.stderr.log").open("w") as err:
                async with (
                    stdio_client(params, errlog=err) as (reader, writer),
                    ClientSession(reader, writer) as session,
                ):
                    try:
                        return await episode(session, agent, arm)
                    except Exception:
                        # Closure requests are safe to repeat; act/look are not.
                        await session.call_tool(
                            "finish",
                            {
                                "outcome": "aborted",
                                "reason": "Controller infrastructure error.",
                            },
                        )
                        raise

        write_json(output / "controller.json", meta)
        try:
            outcome = asyncio.run(connect())
            meta.update(
                status="completed",
                agent_outcome=outcome,
                formal_finish_observed=True,
                exit_code=0,
            )
        except Exception as exc:
            error = exc
            while isinstance(error, BaseExceptionGroup):
                error = error.exceptions[0]
            meta.update(
                status="failed",
                failure_type=type(error).__name__,
                reason=str(error)[:800],
                formal_finish_observed=False,
                exit_code=1,
            )
        meta.update(finished_at=now(), model_calls=dict(counter), robot_s=agent.clock)
        write_json(output / "controller.json", meta)
        return meta["exit_code"]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--instruction", required=True)
    p.add_argument("--mcp-command", required=True)
    p.add_argument("--mcp-args-json", required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--dispatch-policy", choices=("temporal", "serial"), required=True)
    p.add_argument("--model", required=True, help="Fast model identifier")
    p.add_argument("--reasoning-effort", default="low")
    p.add_argument(
        "--fast-vision-port", type=int, help="Use the loopback Qwen2.5-VL server"
    )
    p.add_argument("--planner-model", required=True)
    p.add_argument("--planner-reasoning-effort", default="high")
    p.add_argument("--model-provider-profile")
    p.add_argument("--model-provider-gateway")
    p.add_argument("--execution-clock-command-json")
    p.set_defaults(timeout=0, deadline_unix=None)
    return run(p.parse_args())


if __name__ == "__main__":
    sys.exit(main())
