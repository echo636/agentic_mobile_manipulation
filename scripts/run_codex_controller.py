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
from manipulation_agent.deadline import EpisodeDeadline


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--model", required=True)
    p.add_argument("--instruction", required=True)
    p.add_argument("--mcp-command", required=True)
    p.add_argument("--mcp-args-json", required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--timeout", type=int, default=900)
    p.add_argument('--deadline-unix', type=float,
                   help='Absolute task deadline including simulator startup; limits the remaining model time')
    p.add_argument('--isolate-client-storage', action='store_true',
                   help='Use per-run native sessions and logs without changing HOME, CODEX_HOME or authentication')
    p.add_argument('--agent-profile', choices=['minimal','skills','workflow'], default='skills')
    args = p.parse_args()
    deadline=EpisodeDeadline(args.deadline_unix) if args.deadline_unix is not None else EpisodeDeadline.from_env()
    if args.timeout<=0:p.error('--timeout must be positive')
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
               # A remote server can need more than Codex's optional 1s catalog grace.
               "-c", "mcp_optional_startup_grace_ms=0",
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
              # Never start the policy with an empty manipulation tool catalog.
              "required": True,
              "tool_timeout_sec": 300, "default_tools_approval_mode": "approve"}
    # JSON strings/arrays are also valid TOML values; these are subprocess arguments, not shell text.
    for key, value in server.items():
        command += ["-c", f"mcp_servers.manipulation.{key}=" + json.dumps(value)]
    command += ["-"]
    metadata = {"started_at": now(), "status": "running", "model": args.model, "observation_mode": "rgb_only",
                "episode_deadline_unix":deadline.unix,"requested_timeout_seconds":args.timeout,
                "agent_profile": args.agent_profile,
                "host": os.uname().nodename, "controller": "codex_cli", "command": command,
                "instruction": args.instruction, "source": source_version(),
                "client_version": subprocess.check_output(["codex", "--version"], text=True).strip()}
    if native_sessions is not None:
        metadata['client_storage'] = {'sessions': str(native_sessions), 'sqlite': str(state),
                                      'auth_copied': False, 'home_changed': False}
    write_json(args.output / "controller.json", metadata)
    def expired_before_policy(stage):
        metadata.update(status='failed',timeout=True,termination_reason='episode_deadline_exceeded',
                        failure_stage=stage,finished_at=now(),formal_finish_observed=False,
                        tools_called=[],task_success=None,exit_code=124,effective_timeout_seconds=0)
        write_json(args.output/'controller.json',metadata)
        return 124
    if deadline.expired:return expired_before_policy('before_mcp_handshake')
    try:
        mcp_command=args.mcp_command; mcp_args=json.loads(args.mcp_args_json)
        if native_sessions is not None:
            # Validate the same namespace the model will use, including SSH's
            # access to /dev/null. A host-only handshake misses mount failures.
            prefix=command[:command.index('--')]
            mcp_command=prefix[0];mcp_args=prefix[1:]+['--',args.mcp_command,*mcp_args]
        metadata['mcp_preflight']=check_server(mcp_command,mcp_args,tool_specs(args.agent_profile),args.output,
                                               timeout=deadline.remaining(90))
        metadata['mcp_preflight']['inside_client_storage_namespace']=native_sessions is not None
    except Exception as exc:
        if deadline.expired:return expired_before_policy('mcp_handshake')
        metadata.update(status='failed',failure_stage='mcp_handshake',failure_type=type(exc).__name__,
                        finished_at=now(),formal_finish_observed=False,tools_called=[],task_success=None)
        write_json(args.output/'controller.json',metadata)
        return 2
    if deadline.expired:return expired_before_policy('before_model_start')
    metadata['effective_timeout_seconds']=deadline.remaining(args.timeout)
    metadata['policy_started_at']=now();write_json(args.output/'controller.json',metadata)
    started = time.monotonic()
    with (args.output / "model_events.jsonl").open("w") as stdout, (args.output / "client.stderr.log").open("w") as stderr:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr, text=True,
                                   start_new_session=True)
        metadata["pid"] = process.pid
        write_json(args.output / "controller.json", metadata)
        try:
            process.communicate("Use only the manipulation MCP tools to complete this simulation task.\n" + args.instruction,
                                timeout=deadline.remaining(args.timeout))
            policy_finished_at=time.time()
        except subprocess.TimeoutExpired:
            policy_finished_at=time.time()
            try:os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:pass
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                try:os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:pass
                process.wait(timeout=20)
            metadata["timeout"] = True
            metadata['termination_reason']='episode_deadline_exceeded' if deadline.unix is not None and policy_finished_at>=deadline.unix else 'controller_timeout'
    metadata['policy_finished_at_unix']=policy_finished_at
    # Killing the model may leave its final JSONL row incomplete. Preserve raw
    # bytes and the timeout record instead of losing closure to JSONDecodeError.
    events=[];invalid_lines=[]
    for number,line in enumerate((args.output/'model_events.jsonl').read_text().splitlines(),1):
        if not line.startswith('{'):continue
        try:events.append(json.loads(line))
        except ValueError:invalid_lines.append(number)
    if invalid_lines:metadata['event_decode_errors']={'line_numbers':invalid_lines,'raw_preserved':True}
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
    if deadline.unix is not None and policy_finished_at>=deadline.unix:
        metadata.update(timeout=True,termination_reason='episode_deadline_exceeded')
    metadata.update(status="passed" if process.returncode == 0 and closed and not metadata.get('timeout') else "failed", exit_code=process.returncode,
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
    return process.returncode or (124 if metadata.get('timeout') else 0 if closed else 2)


if __name__ == "__main__":
    raise SystemExit(main())
