"""Antigravity CLI (`agy`) adapter with bwrap config isolation, PreToolUse MCP-only gate, and RGB delivery."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
from uuid import UUID

from .base import ClientAdapter
from .types import PreparedProject

HOOK_SCRIPT = '''#!/usr/bin/env python3
import json
from pathlib import Path
import sys

payload = json.load(sys.stdin)
tc = payload.get("toolCall") or {}
tool = str(tc.get("name") or "")
args = tc.get("args") or {}

ALLOWED_TOOLS = __ALLOWED_TOOLS_JSON__
ISOLATED_CLI_DIR = str(Path.home() / ".gemini" / "antigravity-cli")

if tool == "call_mcp_tool":
    server = args.get("ServerName")
    target = args.get("ToolName")
    if server == "manipulation" and (not ALLOWED_TOOLS or target in ALLOWED_TOOLS):
        print(json.dumps({"decision": "allow"}))
        sys.exit(0)
    print(json.dumps({
        "decision": "deny",
        "reason": (
            f"MCP call to ServerName={server!r} ToolName={target!r} is not permitted. "
            f"Use ServerName='manipulation' with one of: {sorted(ALLOWED_TOOLS)}."
        ),
    }))
    sys.exit(0)

if tool == "read_file":
    raw_path = str(args.get("Path") or "")
    try:
        resolved = str(Path(raw_path).expanduser().resolve(strict=False))
    except Exception:
        resolved = ""
    mcp_schema_prefix = ISOLATED_CLI_DIR + "/mcp/manipulation/"
    brain_prefix = ISOLATED_CLI_DIR + "/brain/"
    if resolved.startswith(mcp_schema_prefix) or (
        resolved.startswith(brain_prefix) and "/.system_generated/steps/" in resolved
    ):
        print(json.dumps({"decision": "allow"}))
        sys.exit(0)

for prefix in ("mcp_manipulation_", "mcp__manipulation__", "manipulation_"):
    if tool.startswith(prefix):
        target = tool[len(prefix):]
        if not ALLOWED_TOOLS or target in ALLOWED_TOOLS:
            print(json.dumps({"decision": "allow"}))
            sys.exit(0)

print(json.dumps({
    "decision": "deny",
    "reason": (
        f"Built-in tool {tool!r} is disabled in robot policy evaluation (only isolated MCP schema/media files "
        "under ~/.gemini/antigravity-cli/mcp/manipulation/ may be read). "
        "Do NOT use shell, workspace filesystem, browser, or built-in finish/wait tools. "
        "Use `call_mcp_tool` with `ServerName='manipulation'` (including `ToolName='finish'` to end the episode)."
    ),
}))
'''


MCP_TOOL_TIMEOUT_SECONDS = 2400  # must exceed the executor stall guard (LocalLimits.wall_seconds) with margin


class AgyAdapter(ClientAdapter):
    name = "agy"
    fallback_paths = ("~/.gemini/bin/agy", "~/.local/bin/agy")
    required_flags = (
        "--output-format",
        "--disable-slash-commands",
        "--dangerously-skip-permissions",
        "--conversation",
        "--model",
    )

    def prepare_resume(self, project: PreparedProject, thread_id: str, prompt: str) -> PreparedProject:
        """Resume a recorded Agy conversation against the same live MCP episode."""
        UUID(thread_id)
        prefix = project.mcp_prefix
        argv = list(project.argv)
        if prefix:
            if argv[: len(prefix) + 1] != [*prefix, "--"]:
                raise ValueError("Agy resume namespace prefix differs from original project")
            argv = argv[len(prefix) + 1 :]
        if not argv or not argv[-1].startswith("-p="):
            raise ValueError("Agy resume requires an original argv ending with -p=...")
        base_argv = [arg for i, arg in enumerate(argv[:-1]) if arg != "--conversation" and (i == 0 or argv[i - 1] != "--conversation")]
        command = [*base_argv, "--conversation", thread_id, f"-p={prompt}"]
        if prefix:
            command = [*prefix, "--", *command]
        return PreparedProject(
            command,
            project.cwd,
            None,
            env_overlay=project.env_overlay,
            native_sessions=project.native_sessions,
            client_storage=project.client_storage,
            mcp_prefix=prefix,
        )

    def prepare_handoff(self, project: PreparedProject, prompt: str) -> PreparedProject:
        """Start a fresh Agy conversation (0 prior images) against the same live MCP episode."""
        prefix = project.mcp_prefix
        argv = list(project.argv)
        if prefix:
            if argv[: len(prefix) + 1] != [*prefix, "--"]:
                raise ValueError("Agy handoff namespace prefix differs from original project")
            argv = argv[len(prefix) + 1 :]
        if not argv or not argv[-1].startswith("-p="):
            raise ValueError("Agy handoff requires an original argv ending with -p=...")
        base_argv = [
            arg for i, arg in enumerate(argv[:-1])
            if arg != "--conversation" and (i == 0 or argv[i - 1] != "--conversation")
        ]
        agy_prompt = (
            "Use ONLY `call_mcp_tool` with `ServerName='manipulation'` (including `ToolName='finish'` to end the episode) "
            "to complete this simulation task.\n" + prompt
        )
        command = [*base_argv, f"-p={agy_prompt}"]
        if prefix:
            command = [*prefix, "--", *command]
        return PreparedProject(
            command,
            project.cwd,
            None,
            env_overlay=project.env_overlay,
            native_sessions=project.native_sessions,
            client_storage=project.client_storage,
            mcp_prefix=prefix,
        )

    def prepare_project(self, config, instructions: str, tool_names: list[str]) -> PreparedProject:
        wrapper = shutil.which("bwrap")
        if not wrapper:
            raise RuntimeError("AgyAdapter requires bubblewrap (`bwrap`) to isolate ~/.gemini/config/mcp_config.json safely")
        executable = self.resolve_executable() or self.name

        workspace = (config.output / "agy_project").resolve()
        (workspace / ".agents").mkdir(parents=True)
        isolated_root = (config.output / "agy_isolated_gemini").resolve()
        isolated_cli = isolated_root / "antigravity-cli"
        isolated_cli.mkdir(parents=True)

        # Ensure host mount target files/dirs exist so bwrap --ro-bind / --bind succeeds cleanly without mutating them.
        user_gemini = Path.home() / ".gemini"
        host_config_dir = user_gemini / "config"
        host_config_dir.mkdir(parents=True, exist_ok=True)
        for fname in ("mcp_config.json", "config.json"):
            target_file = host_config_dir / fname
            if not target_file.exists():
                target_file.write_text("{}\n")

        host_cli_dir = user_gemini / "antigravity-cli"
        if not host_cli_dir.is_dir():
            raise RuntimeError(f"Expected existing Antigravity CLI state directory at {host_cli_dir}; run `agy` sign-in first")

        # Bind read-only OAuth/device identity files onto ~/.gemini/antigravity-cli/<fname> inside the namespace
        # AFTER binding the per-run antigravity-cli directory! (Never copy secret bytes into output.)
        ro_auth_binds: list[str] = []
        for fname in ("antigravity-oauth-token", "installation_id", "jetski_state.pbtxt", "settings.json"):
            src = host_cli_dir / fname
            if src.is_file():
                (isolated_cli / fname).write_text("")
                ro_auth_binds += ["--ro-bind", str(src.resolve()), str((host_cli_dir / fname).resolve())]
        builtin_src = host_cli_dir / "builtin"
        if builtin_src.is_dir():
            (isolated_cli / "builtin").mkdir()
            ro_auth_binds += ["--ro-bind", str(builtin_src.resolve()), str((host_cli_dir / "builtin").resolve())]

        # Write per-episode MCP config and global config (disabling plugins like swarm).
        mcp_env = {}
        if pythonpath := os.environ.get("PYTHONPATH"):
            mcp_env["PYTHONPATH"] = pythonpath
        for key in config.mcp_env_vars:
            if val := os.environ.get(key):
                mcp_env[key] = val

        mcp_server_spec: dict = {
            "command": config.mcp_command,
            "args": config.mcp_args,
            # agy's own per-call MCP deadline defaults to 3 min, shorter than one executor action (1800 s guard) or a
            # finish-evidence judge call; the client must not cut a tool call the executor is still running.
            "timeoutSeconds": MCP_TOOL_TIMEOUT_SECONDS,
        }
        if mcp_env:
            mcp_server_spec["env"] = mcp_env

        mcp_cfg_path = isolated_root / "mcp_config.json"
        mcp_cfg_path.write_text(json.dumps({"mcpServers": {"manipulation": mcp_server_spec}}, indent=2) + "\n")

        global_cfg_path = isolated_root / "config.json"
        global_cfg_path.write_text(json.dumps({
            "plugins": {"swarm": {"enabled": False}},
            "userSettings": {
                "autoExecutionPolicy": "CASCADE_COMMANDS_AUTO_EXECUTION_EAGER",
                "permissionPreset": "AGENT_PERMISSION_PRESET_TURBO",
            },
        }, indent=2) + "\n")

        # Write PreToolUse hook enforcing MCP-only boundary (blocks built-in finish, wait, run_command, view_file, etc.).
        hook_py = isolated_root / "mcp_only_gate.py"
        hook_py.write_text(HOOK_SCRIPT.replace("__ALLOWED_TOOLS_JSON__", json.dumps(list(tool_names))))
        hook_py.chmod(0o755)
        (workspace / ".agents" / "hooks.json").write_text(json.dumps({
            "mcp-only-gate": {
                "PreToolUse": [
                    {
                        "matcher": "*",
                        "hooks": [{"type": "command", "command": f"{sys.executable} {hook_py}", "timeout": 10}],
                    }
                ]
            }
        }, indent=2) + "\n")

        # Write workspace rule GEMINI.md with policy instructions AND concise MCP call_mcp_tool argument signatures
        # so lazy-MCP models know exact argument keys immediately without wasting turns guessing schemas.
        schema_lines = [
            "## Exact `call_mcp_tool` Argument Signatures (`ServerName: \"manipulation\"`)",
            "Call `call_mcp_tool` with `ServerName: \"manipulation\"`, `ToolName` set to the tool named on each line, "
            "and the `Arguments` shown there. `Arguments` is a JSON object whose keys are the tool's own fields; "
            "do not put it in quotes as a string and do not nest another `Arguments` inside it.",
            "- `initialize`: `{\"Arguments\": {}}`",
            "- `act`: `{\"Arguments\": {\"primitive\": \"<grasp|place_on_top|place_inside|place_next_to|place_under|open|close|toggle_on|toggle_off|navigate_to|cut|wipe|sweep|vacuum|spray|spread|soak|pour|scoop|attach|hang|fold|release|wait>\", \"target\": {\"image_ref\": \"rgb-00001-front\", \"point\": [0.5, 0.5]} /* or null for release/wait */, \"revision\": 0, \"placement_yaw_degrees\": null, \"placement_roll_degrees\": null, \"wait_seconds\": null}}`",
            "- `look`: `{\"Arguments\": {\"yaw_degrees\": 45, \"revision\": 0}}` (`yaw_degrees` in `[-90, 90]`, positive=left, negative=right)",
            "- `finish`: `{\"Arguments\": {\"outcome\": \"<achieved|blocked|aborted>\", \"reason\": \"...\"}}`",
        ]
        if "zoom" in tool_names:
            schema_lines.append(
                "- `zoom`: `{\"Arguments\": {\"image_ref\": \"rgb-00001-front\", \"point\": [0.5, 0.5]}}`; then `act` may use "
                "`\"target\": {\"zoom_ref\": \"zoom-00001\", \"point\": [0.5, 0.5]}` (point in the zoomed image)"
            )
        if "list_skills" in tool_names:
            schema_lines.append("- `list_skills`: `{\"Arguments\": {}}`")
        if "read_skill" in tool_names:
            schema_lines.append("- `read_skill`: `{\"Arguments\": {\"name\": \"visual-manipulation\", \"resource\": \"SKILL.md\"}}`")
        if "schedule_plan" in tool_names:
            schema_lines.append(
                "- `schedule_plan`: `{\"Arguments\": {\"plan\": {\"places\": [\"kitchen\", \"living_room\"], "
                "\"timed_processes\": [{\"id\": \"heat_job\", \"place\": \"kitchen\"}], "
                "\"carriers\": [{\"id\": \"tray\", \"place\": \"kitchen\"}], "
                "\"deliveries\": [{\"id\": \"serve_batch\", \"from_place\": \"kitchen\", \"to_place\": \"living_room\", \"item_count\": 4}]}, "
                "\"completed_job_ids\": [], \"verified_closed_compartments\": []}}` "
                "(or inspect `~/.gemini/antigravity-cli/mcp/manipulation/schedule_plan.json` via `read_file`)"
            )
        if "plan_task_graph" in tool_names:
            schema_lines.append(
                "- `plan_task_graph`: `{\"Arguments\": {\"graph\": {\"agent_location\": \"L1\", "
                "\"locations\": [{\"id\": \"L1\"}, {\"id\": \"L2\"}], "
                "\"travel_cost\": [{\"from_location\": \"L1\", \"to_location\": \"L2\", \"cost_s\": 20.0}], "
                "\"state_vars\": [{\"name\": \"v1\", \"value\": 0}], "
                "\"entities\": [{\"id\": \"e1\", \"location\": \"L1\", \"movable\": true, \"capacity\": 0}], "
                "\"operations\": [{\"id\": \"op1\", \"location\": \"L1\", \"effects\": [{\"var\": \"v1\", \"value\": 1}], \"active_duration_s\": 10.0}], "
                "\"goals\": {\"predicates\": [{\"var\": \"v1\", \"value\": 1}], \"entity_locations\": [{\"entity_id\": \"e1\", \"location\": \"L2\"}]}}, "
                "\"completed_operations\": []}}`"
            )
        rule_header = (
            "# EMBODIED POLICY EVALUATION INSTRUCTIONS\n\n"
            "You are an embodied robot policy under evaluation in a simulator, NOT a coding assistant.\n"
            "- Workspace filesystem, shell (`run_command`), browser, and built-in `finish`/`wait` tools are disabled.\n"
            "- Invoke EVERY robot and skill tool via `call_mcp_tool` with `ServerName: \"manipulation\"` and "
            f"`ToolName` in `{list(tool_names)}` (including `ToolName: \"finish\"` when ending the episode).\n"
            + "\n".join(schema_lines) + "\n\n"
        )
        rule_text = rule_header + instructions + "\n"
        if len(rule_text.encode("utf-8")) > 23500:
            raise ValueError(f"Agy GEMINI.md rule exceeds 24 KB inline limit ({len(rule_text.encode('utf-8'))} bytes)")
        (workspace / "GEMINI.md").write_text(rule_text)

        prefix = [
            wrapper,
            "--die-with-parent",
            "--bind", "/", "/",
            "--dev-bind", "/dev", "/dev",
            "--proc", "/proc",
            "--ro-bind", str(mcp_cfg_path), str((host_config_dir / "mcp_config.json").resolve()),
            "--ro-bind", str(global_cfg_path), str((host_config_dir / "config.json").resolve()),
            "--bind", str(isolated_cli), str(host_cli_dir.resolve()),
            *ro_auth_binds,
        ]

        agy_cmd = [
            executable,
            "--output-format", "stream-json",
            "--disable-slash-commands",
            "--dangerously-skip-permissions",
            "--model", config.model,
        ]
        if config.reasoning_effort is not None:
            effort = "max" if config.reasoning_effort == "ultra" else config.reasoning_effort
            agy_cmd += ["--effort", effort]

        agy_prompt = (
            "Use ONLY `call_mcp_tool` with `ServerName='manipulation'` (including `ToolName='finish'` to end the episode) "
            "to complete this simulation task.\n" + config.instruction
        )
        agy_cmd.append(f"-p={agy_prompt}")

        command = [*prefix, "--", *agy_cmd]
        storage = {
            "isolated_cli_dir": str(isolated_cli),
            "mcp_config": str(mcp_cfg_path),
            "auth_copied": False,
            "home_changed": False,
        }
        return PreparedProject(
            command,
            workspace,
            None,
            native_sessions=isolated_cli / "conversations",
            client_storage=storage,
            mcp_prefix=prefix,
        )
