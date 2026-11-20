"""RAX-style Oracle exploration lifecycle. prepare/status never call a model.

An operator supplies immutable resource-leased environment-only episode manifests.
The agent writes code through bounded workspace MCP tools; Python runs offline.
"""
import argparse
from dataclasses import asdict
import json
import shutil
import hashlib
import os
from pathlib import Path
import sys
import time
from manipulation_agent.clients import ClientConfig, get_adapter
from manipulation_agent.clients.types import PreparedProject
from manipulation_agent.exploration_workspace import WORKFLOW
from manipulation_agent.exploration_session import prepare_session, ExplorationSession
from manipulation_agent.exploration_factory import BridgeFactory
from manipulation_agent.exploration_frontend import tool_specs
from manipulation_agent.records import write_json, now
from manipulation_agent.tools import tool_specs as robot_specs
from manipulation_agent.mcp_preflight import check_server
from manipulation_agent.exploration_budget import DEFAULT_CONTEXT_POLICY, context_policy, tune_prepared_project
from manipulation_agent.exploration_subtasks import SubtaskCheckpoints
from manipulation_agent.exploration_qualification import DEFAULT_QUALIFICATION
from manipulation_agent.manip_runtime import ManipRecipes

def mcp_host_environment():
    """Keep the user's service bus reachable after Codex filters MCP's env.

    Resource admission and environment-only workers use systemctl --user.
    These two non-secret coordinates are explicit argv values, like PYTHONPATH;
    do not forward the entire host environment or alter the account's limits.
    """
    return [key + "=" + os.environ[key]
            for key in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS")
            if os.environ.get(key)]

def recorded_thread(output):
    """Recover a startup header after SIGKILL without reading a full RGB history."""
    if not output:
        return None
    try:
        with (Path(output)/"model_events.jsonl").open("rb") as stream:
            remaining = 1024 * 1024
            while remaining > 0:
                line = stream.readline(remaining)
                remaining -= len(line)
                if not line:
                    break
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if isinstance(event, dict) and event.get("type") == "thread.started":
                    thread = event.get("thread_id")
                    if isinstance(thread, str) and thread:
                        return thread
    except OSError:
        pass
    return None


def enter_snapshot(cfg, argv):
    """Evaluate/register with the same modules as the environment and MCP owner."""
    entry = Path(cfg["source_snapshot"]).resolve()/"scripts/run_exploration_pipeline.py"
    loaded = Path(sys.modules[ExplorationSession.__module__].__file__).resolve()
    if entry != Path(__file__).resolve() or loaded.parent != entry.parents[1]/"src/manipulation_agent":
        if not entry.is_file():
            raise ValueError("Frozen pipeline entrypoint is missing")
        env = {**os.environ, "PYTHONPATH": str(entry.parents[1]/"src"),
               "PYTHONDONTWRITEBYTECODE": "1"}
        os.execve(sys.executable, [sys.executable, str(entry), *argv], env)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("operation",choices=["prepare","status","run","evaluate"])
    p.add_argument("--session",type=Path,required=True)
    p.add_argument("--config",type=Path)
    p.add_argument("--provider")
    p.add_argument("--resume",action="store_true")
    p.add_argument("--fresh-thread",action="store_true")
    args=p.parse_args(argv)
    if args.resume and args.fresh_thread:p.error("Choose resume or fresh-thread, not both")
    root=args.session.resolve()
    if args.operation=="prepare":
        if args.config is None:p.error("--config is required")
        cfg=json.loads(args.config.read_text())
        source = Path(cfg.get("source_path", Path(__file__).resolve().parents[1])).resolve()
        cfg.setdefault('qualification', dict(DEFAULT_QUALIFICATION))
        cfg['session_workspace'] = str(root / 'workspace')
        if cfg['qualification'].get('metric') == 'engineering_success':
            evaluator = Path(cfg['qualification']['evaluator_path']).resolve()
            if evaluator.is_relative_to(root / 'workspace'):
                raise ValueError('Acceptance evaluator must be outside the agent workspace')
            cfg['qualification']['evaluator_sha256'] = hashlib.sha256(evaluator.read_bytes()).hexdigest()
        cfg.setdefault("robot_tools",robot_specs("oracle_waypoints"))
        cfg.setdefault("model","gpt-6-astra");cfg.setdefault("reasoning_effort","high")
        cfg['context_policy'] = {**DEFAULT_CONTEXT_POLICY, **cfg.get('context_policy', {})}
        if not root.is_relative_to(Path(cfg["data_root"]).resolve()):
            p.error("Session must reside under configured NAS/data root")
        # A worker section uses the maintained, environment-only launcher.
        # Freeze the effective project sources once before any model or GPU call.
        schedules = cfg["exploration"] + cfg["formal"]
        prepare_session(root,cfg,tool_specs(cfg["robot_tools"]))
        snapshot = root / "source_snapshot"
        snapshot.mkdir()
        files = {}
        for folder in ("src", "scripts", "program_skills", "skills"):
            origin = source / folder
            if not origin.is_dir():
                continue
            for candidate in origin.rglob("*"):
                if candidate.is_symlink():
                    raise ValueError("Source snapshots cannot contain symlinks")
                if not candidate.is_file() or "__pycache__" in candidate.parts or candidate.suffix == ".pyc":
                    continue
                relative = candidate.relative_to(source)
                data = candidate.read_bytes()
                target = snapshot / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                files[relative.as_posix()] = hashlib.sha256(data).hexdigest()
        write_json(snapshot / "source_manifest.json", files)
        cfg["source_snapshot"] = str(snapshot)
        if cfg['qualification'].get('metric') == 'engineering_success':
            evaluator = Path(cfg['qualification']['evaluator_path']).resolve()
            if evaluator.is_relative_to(source):
                cfg['qualification']['evaluator_path'] = str(snapshot / evaluator.relative_to(source))
        if cfg.get('seed_manip_recipes', True):
            seeded = ManipRecipes(snapshot / 'program_skills/rax').copy_to(root / 'workspace/code')
            with (root / 'workspace/memory/INDEX.md').open('a') as index_file:
                index_file.write('\nManip draft projects: '+json.dumps(seeded.catalog())+'\n'
                                 'Use list_manip_recipes/read_manip_recipe after navigation. Edit only workspace sources.\n')
        factory=BridgeFactory(cfg["data_root"])
        for index,spec in enumerate(schedules):
            if "worker" in spec:
                worker=spec.pop("worker")
                worker.setdefault('skip_gpu_memory_admission', True)
                if worker['skip_gpu_memory_admission']:
                    worker['minimum_free_gpu_mib'] = 0
                worker.update(run_dir=spec["run_dir"],identity=spec["identity"],
                              source_path=str(snapshot),data_root=cfg["data_root"],instruction=cfg["task"])
                manifest=root/"worker_manifests"/(str(index)+".json")
                manifest.parent.mkdir(exist_ok=True)
                write_json(manifest,worker)
                spec["launch_command"]=["/usr/bin/env","PYTHONPATH="+str(snapshot/"src"),
                    worker["python"],"-u",str(snapshot/"scripts/run_exploration_episode.py"),
                    "--manifest",str(manifest)]
                spec["launcher_contract"]="resource_leased_environment_only"
            factory.validate(spec)
        write_json(root/"config.json",cfg)
        state=json.loads((root/"state.json").read_text())
        state["config_sha256"]=hashlib.sha256((root/"config.json").read_bytes()).hexdigest()
        write_json(root/"state.json",state)
        print(json.dumps({"prepared":str(root),"model_called":False}))
        return 0
    cfg=json.loads((root/"config.json").read_text())
    if args.operation=="status":
        state=json.loads((root/"state.json").read_text())
        print(json.dumps({k:state[k] for k in ("phase","active","exploration_used","formal_used","submitted")}))
        return 0
    enter_snapshot(cfg, list(argv) if argv is not None else sys.argv[1:])
    if args.operation=="evaluate":
        session=ExplorationSession(root,BridgeFactory(cfg["data_root"]))
        try:print(json.dumps(session.evaluate()))
        finally:session.close()
        return 0
    if args.provider is None:p.error("--provider explicitly selects the existing API profile")
    # One coding-agent owner per session. Never relaunch or retry automatically.
    import fcntl
    with (root/"agent_owner.lock").open("a") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        state=json.loads((root/"state.json").read_text())
        if state["phase"]!="exploring":p.error("Exploration is closed; use evaluate")
        previous=root/"agent.json"
        saved=json.loads(previous.read_text()) if previous.exists() else None
        rotations=SubtaskCheckpoints(root)
        pending_rotation=None
        if args.resume and saved and not saved.get("thread_id"):
            thread = recorded_thread(saved.get("output"))
            if thread:
                saved["thread_id"] = thread
                write_json(previous, saved)
        if saved and not (args.resume or args.fresh_thread):
            p.error("Existing agent: explicitly --resume, or use --fresh-thread after a checkpoint")
        if args.resume and (not saved or not saved.get("thread_id")):p.error("No resumable model thread")
        if args.fresh_thread:
            if not saved:p.error("Fresh conversation requires a checkpointing predecessor")
            try:pending_rotation=rotations.validate_fresh_start(saved,args.provider)
            except ValueError as error:p.error(str(error))
            history=root/"agent_history";history.mkdir(exist_ok=True)
            write_json(history/(str(time.time_ns())+".json"),saved)
        output=root/"agents"/str(time.time_ns())
        output.mkdir(parents=True)
        # Preserve host auth location. All new runtime storage remains under NAS.
        os.environ["MAS_CLIENT_STATE_ROOT"]=str(root/"client_state")
        os.environ["MAS_CLIENT_MOUNT_STRATEGY"]="root"
        adapter=get_adapter("codex")
        policy = context_policy(cfg)
        initial_prompt = (
            "This is a FRESH conversation after a verified subtask checkpoint; old chat history is intentionally absent. "
            "Call take_notes({}) and status, inspect the current observation without resetting, then read only code and skills relevant to next_subtask. "
            if args.fresh_thread else
            "Read the workspace AGENTS.md and TASK.md with read_workspace_file, call status, ")
        config=ClientConfig("codex",cfg["model"],initial_prompt +
            "then explore, write code, rehearse and submit your exact frozen controller. "
            "Use run_development/rehearse as blocking calls; do not repeatedly poll. "
            "Select Oracle information with set_oracle_focus/get_oracle_geometry. "
            "All robot interaction goes through this session. Do not start a new episode on resume.",
            "/usr/bin/env",[*mcp_host_environment(), "PYTHONPATH="+str(Path(cfg["source_snapshot"])/"src"),
                "PYTHONDONTWRITEBYTECODE=1", sys.executable,
                "-m","manipulation_agent.exploration_frontend","--session",str(root)],
            output,None,"oracle_waypoints",True,cfg["reasoning_effort"],args.provider,
            blocking_tool_yield_ms=policy['blocking_tool_yield_ms'],
            auto_compact_token_limit=policy['auto_compact_token_limit'])
        specs=tool_specs(cfg["robot_tools"])
        if args.resume:
            if saved.get("provider") != args.provider:
                p.error("Resume keeps the recorded provider profile; do not silently change it")
            original = saved["prepared_project"]
            project = PreparedProject(
                argv=original["argv"], cwd=Path(original["cwd"]) if original["cwd"] else None,
                stdin=original["stdin"], env_overlay=original["env_overlay"],
                native_sessions=Path(original["native_sessions"]) if original["native_sessions"] else None,
                client_storage=original["client_storage"], mcp_prefix=original["mcp_prefix"])
        else:
            project=adapter.prepare_project(config,WORKFLOW,[t["name"] for t in specs])
            project.argv[-1:-1] = ["-c", f"model_providers.{args.provider}.request_max_retries=0",
                                       "-c", f"model_providers.{args.provider}.stream_max_retries=0"]
            project.env_overlay.update(PYTHONPATH=str(Path(cfg["source_snapshot"])/"src"))
        project = tune_prepared_project(project, cfg, WORKFLOW, [t['name'] for t in specs])
        prepared_record={"argv":project.argv,"cwd":str(project.cwd) if project.cwd else None,
            "stdin":project.stdin,"env_overlay":project.env_overlay,
            "native_sessions":str(project.native_sessions) if project.native_sessions else None,
            "client_storage":project.client_storage,"mcp_prefix":project.mcp_prefix}
        if args.resume:
            project=adapter.prepare_resume(project,saved["thread_id"],
                "Continue the SAME exploration session. Call take_notes({}) and status, "
                "then refresh current observations without resetting. Saved notes may be stale. "
                "Do not replay interrupted/completed calls. Await each MCP call in one functions.exec; "
                "use the configured long default yield, not short yield pragmas or functions.wait polling. "
                "Continue code development and exact-bundle submission.")
        prefix=project.mcp_prefix
        check_server(prefix[0] if prefix else config.mcp_command,
            prefix[1:]+["--",config.mcp_command,*config.mcp_args] if prefix else config.mcp_args,
            specs,output,timeout=90)
        record={"started_at":now(),"status":"running","model":cfg["model"],
                "reasoning_effort":cfg["reasoning_effort"],"output":str(output),
                "context_policy":policy,
                "outer_provider_resumes":0,"provider_request_retries":0,"provider_stream_retries":0,"workspace_access":"bounded_MCP_file_tools",
                "raw_cli_shell_disabled":True,"provider":args.provider,"prepared_project":prepared_record,
                "thread_id":saved.get("thread_id") if saved and args.resume else None,
                "fresh_thread":args.fresh_thread,
                "continued_from_thread":saved.get("thread_id") if saved and args.fresh_thread else None,
                "subtask_checkpoint":pending_rotation}
        write_json(previous,record)
        def remember_thread(event):
            if event.get("type") != "thread.started":
                return
            thread = event.get("thread_id")
            if not isinstance(thread, str) or not thread:
                return
            if record["thread_id"] and record["thread_id"] != thread:
                raise RuntimeError("Resume unexpectedly changed model thread")
            if record["thread_id"] != thread:
                record["thread_id"] = thread
                write_json(previous, record)
                if pending_rotation is not None:
                    record["thread_rotation"] = rotations.complete_rotation(pending_rotation,thread)
                    write_json(previous,record)
        result = None
        try:
            result=adapter.run(project,output,None,on_event=remember_thread)
        finally:
            # Also recover from an interrupted launch before its event callback ran.
            thread = recorded_thread(output)
            if thread and not record["thread_id"]:
                record["thread_id"] = thread
            state=json.loads((root/"state.json").read_text())
            record.update(finished_at=now(),exit_code=result.returncode if result else None,
                          status="submitted" if result and result.returncode==0 and state["phase"]=="submitted" else "paused",
                          task_success=None,phase=state["phase"])
            write_json(previous,record)
        if state["phase"]=="submitted" and result.returncode==0:
            session=ExplorationSession(root,BridgeFactory(cfg["data_root"]))
            try:session.evaluate()
            finally:session.close()
        print(json.dumps(record))
        return result.returncode
if __name__=="__main__":raise SystemExit(main())
