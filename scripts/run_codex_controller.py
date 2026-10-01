"""Run the installed model client against a project MCP endpoint using its existing login.

This is a policy under evaluation, not a coding worker. Shell, plugins, other agents,
web, browser and filesystem image tools are disabled. No authentication file is copied.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import shutil
import signal

from manipulation_agent.tools import tool_specs
from manipulation_agent.vision_policy import system_prompt
from manipulation_agent.records import now, write_json, source_version
from manipulation_agent.model_trace import export_summaries
from manipulation_agent.mcp_preflight import check_server


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--model", required=True)
    p.add_argument("--instruction", required=True)
    p.add_argument("--mcp-command", required=True)
    p.add_argument("--mcp-args-json", required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--timeout", type=int, default=900)
    p.add_argument('--agent-profile', choices=['minimal','skills','workflow','motor','official'], default='skills')
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    shutil.copytree(Path(__file__).resolve().parents[1] / "src", args.output / "source_snapshot",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    workspace = args.output / "empty_workspace"
    workspace.mkdir()
    command = ["codex", "exec", "--ignore-user-config", "--skip-git-repo-check", "--json",
               "--sandbox", "read-only", "--model", args.model, "--cd", str(workspace.resolve()),
               "-c", 'approval_policy="never"', "-c", 'web_search="disabled"',
               "-c", "project_doc_max_bytes=0", "-c", 'model_reasoning_summary="auto"',
               "-c", "developer_instructions=" + json.dumps(system_prompt(args.agent_profile))]
    for feature in ("shell_tool", "unified_exec", "plugins", "apps", "hooks", "view_image", "multi_agent", "browser_use",
                    "computer_use", "image_generation"):
        command += ["--disable", feature]
    server = {"command": args.mcp_command, "args": json.loads(args.mcp_args_json),
              "enabled_tools": [t["name"] for t in tool_specs(args.agent_profile)], "startup_timeout_sec": 60,
              "tool_timeout_sec": 300, "default_tools_approval_mode": "approve"}
    # JSON strings/arrays are also valid TOML values; these are subprocess arguments, not shell text.
    for key, value in server.items():
        command += ["-c", f"mcp_servers.manipulation.{key}=" + json.dumps(value)]
    command += ["-"]
    metadata = {"started_at": now(), "status": "running", "model": args.model, "observation_mode": "rgb_only",
                "agent_profile": args.agent_profile,
                "host": os.uname().nodename, "controller": "codex_cli", "command": command,
                "instruction": args.instruction, "source": source_version(),
                "client_version": subprocess.check_output(["codex", "--version"], text=True).strip()}
    write_json(args.output / "controller.json", metadata)
    try:
        metadata['mcp_preflight']=check_server(args.mcp_command,json.loads(args.mcp_args_json),tool_specs(args.agent_profile),args.output)
    except Exception as exc:
        metadata.update(status='failed',failure_stage='mcp_handshake',failure_type=type(exc).__name__,
                        finished_at=now(),formal_finish_observed=False,tools_called=[],task_success=None)
        write_json(args.output/'controller.json',metadata)
        return 2
    metadata['policy_started_at']=now();write_json(args.output/'controller.json',metadata)
    started = time.monotonic()
    with (args.output / "model_events.jsonl").open("w") as stdout, (args.output / "client.stderr.log").open("w") as stderr:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr, text=True,
                                   start_new_session=True)
        metadata["pid"] = process.pid
        write_json(args.output / "controller.json", metadata)
        try:
            process.communicate("Use only the manipulation MCP tools to complete this simulation task.\n" + args.instruction,
                                timeout=args.timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=20)
            metadata["timeout"] = True
    events = [json.loads(line) for line in (args.output / "model_events.jsonl").read_text().splitlines() if line.startswith("{")]
    closed = False
    calls = []
    for event in events:
        item = event.get("item", {})
        if event.get("type") != "item.completed" or item.get("type") != "mcp_tool_call":
            continue
        calls.append(item.get("tool"))
        if item.get("server") == "manipulation" and item.get("tool") == "finish":
            for content in (item.get("result") or {}).get("content", []):
                if content.get("type") == "text":
                    try:
                        closed = closed or json.loads(content["text"]).get("closed", False)
                    except (ValueError, TypeError):
                        pass
    metadata.update(status="passed" if process.returncode == 0 and closed else "failed", exit_code=process.returncode,
                    duration_seconds=time.monotonic() - started, finished_at=now(),
                    task_success=None, formal_finish_observed=closed, tools_called=calls,
                    validation_note="Client exit alone does not prove task success; join with simulator run.json")
    try:
        metadata['reasoning_trace']=export_summaries(args.output,events,metadata['started_at'],metadata['finished_at'])
    except Exception as exc:
        metadata['reasoning_trace']={'status':'failed','error_type':type(exc).__name__,
                                     'reason':'Run-local summary export failed; raw controller events preserved'}
    write_json(args.output / "controller.json", metadata)
    print(json.dumps({k: metadata[k] for k in ("status", "exit_code", "model", "duration_seconds")}))
    return process.returncode or (0 if closed else 2)


if __name__ == "__main__":
    raise SystemExit(main())
