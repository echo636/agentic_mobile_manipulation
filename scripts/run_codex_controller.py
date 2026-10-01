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
    p.add_argument('--isolate-client-storage', action='store_true',
                   help='Use per-run native sessions and logs without changing HOME, CODEX_HOME or authentication')
    p.add_argument('--agent-profile', choices=['minimal','skills','workflow','official'], default='skills')
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    shutil.copytree(Path(__file__).resolve().parents[1] / "src", args.output / "source_snapshot",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    workspace = args.output / "empty_workspace"
    workspace.mkdir()
    native_sessions = None
    command = ["codex", "exec", "--ignore-user-config", "--skip-git-repo-check", "--json",
               "--sandbox", "read-only", "--model", args.model, "--cd", str(workspace.resolve()),
               "-c", 'approval_policy="never"', "-c", 'web_search="disabled"',
               "-c", "project_doc_max_bytes=0", "-c", 'model_reasoning_summary="auto"',
               "-c", "developer_instructions=" + json.dumps(system_prompt(args.agent_profile))]
    if args.isolate_client_storage:
        # Only this child sees a different sessions directory. The host's original
        # sessions and credential files are untouched. Keep SQLite on local disk.
        wrapper = shutil.which('bwrap')
        if not wrapper:
            raise RuntimeError('Client storage isolation requires bubblewrap')
        native_sessions = (args.output / 'native_sessions').resolve()
        native_sessions.mkdir()
        client_root = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
        session_mount = client_root / 'sessions'
        if not session_mount.is_dir():
            raise RuntimeError('Expected existing native sessions directory')
        local_root = os.environ.get('MAS_CLIENT_STATE_ROOT')
        if not local_root:
            raise RuntimeError('MAS_CLIENT_STATE_ROOT must name project-local writable storage')
        state = Path(local_root).resolve() / args.output.name
        state.mkdir(parents=True, exist_ok=False)
        command += ['-c', 'sqlite_home=' + json.dumps(str(state)),
                    '-c', 'log_dir=' + json.dumps(str((args.output / 'native_logs').resolve()))]
        command = [wrapper, '--die-with-parent', '--bind', '/', '/', '--dev-bind', '/dev', '/dev', '--proc', '/proc', '--bind', str(native_sessions),
                   str(session_mount), '--', *command]
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
    if native_sessions is not None:
        metadata['client_storage'] = {'sessions': str(native_sessions), 'sqlite': str(state),
                                      'auth_copied': False, 'home_changed': False}
    write_json(args.output / "controller.json", metadata)
    try:
        mcp_command=args.mcp_command; mcp_args=json.loads(args.mcp_args_json)
        if native_sessions is not None:
            # Validate the same namespace the model will use, including SSH's
            # access to /dev/null. A host-only handshake misses mount failures.
            prefix=command[:command.index('--')]
            mcp_command=prefix[0];mcp_args=prefix[1:]+['--',args.mcp_command,*mcp_args]
        metadata['mcp_preflight']=check_server(mcp_command,mcp_args,tool_specs(args.agent_profile),args.output)
        metadata['mcp_preflight']['inside_client_storage_namespace']=native_sessions is not None
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
        metadata['reasoning_trace']=export_summaries(args.output,events,metadata['started_at'],metadata['finished_at'],sessions_root=native_sessions)
    except Exception as exc:
        metadata['reasoning_trace']={'status':'failed','error_type':type(exc).__name__,
                                     'reason':'Run-local summary export failed; raw controller events preserved'}
    write_json(args.output / "controller.json", metadata)
    print(json.dumps({k: metadata[k] for k in ("status", "exit_code", "model", "duration_seconds")}))
    return process.returncode or (0 if closed else 2)


if __name__ == "__main__":
    raise SystemExit(main())
