"""Claude Code CLI adapter with strict MCP-only sandboxing and native RGB ImageContent delivery."""
from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import UUID

from .base import ClientAdapter
from .types import PreparedProject


class ClaudeAdapter(ClientAdapter):
    name = "claude"
    fallback_paths = ("~/.nvm/versions/node/v24.19.0/bin/claude", "~/.local/bin/claude")
    required_flags = (
        "--output-format",
        "--strict-mcp-config",
        "--mcp-config",
        "--allowedTools",
        "--append-system-prompt",
        "--tools",
        "--resume",
    )

    def prepare_resume(self, project: PreparedProject, thread_id: str, prompt: str) -> PreparedProject:
        """Resume a recorded Claude Code session against the same live MCP episode."""
        UUID(thread_id)
        prefix = project.mcp_prefix
        argv = list(project.argv)
        if prefix:
            if argv[: len(prefix) + 1] != [*prefix, "--"]:
                raise ValueError("Claude resume namespace prefix differs from original project")
            argv = argv[len(prefix) + 1 :]
        if not argv:
            raise ValueError("Claude resume requires a valid base argv")
        # Strip trailing positional prompt if any, insert --resume <thread_id>, keep stdin prompt
        command = [*argv, "--resume", thread_id]
        if prefix:
            command = [*prefix, "--", *command]
        return PreparedProject(
            command,
            project.cwd,
            prompt,
            env_overlay=project.env_overlay,
            native_sessions=project.native_sessions,
            client_storage=project.client_storage,
            mcp_prefix=prefix,
        )

    def prepare_project(self, config, instructions: str, tool_names: list[str]) -> PreparedProject:
        if config.isolate_client_storage:
            raise ValueError("Claude storage isolation via bwrap is optional; run without --isolate-client-storage")
        executable = self.resolve_executable() or self.name
        workspace = (config.output / "claude_project").resolve()
        workspace.mkdir()

        mcp_env = {}
        if pythonpath := os.environ.get("PYTHONPATH"):
            mcp_env["PYTHONPATH"] = pythonpath
        for key in config.mcp_env_vars:
            if val := os.environ.get(key):
                mcp_env[key] = val

        mcp_server_spec: dict = {
            "type": "stdio",
            "command": config.mcp_command,
            "args": config.mcp_args,
        }
        if mcp_env:
            mcp_server_spec["env"] = mcp_env

        mcp_path = workspace / "mcp.json"
        mcp_path.write_text(json.dumps({"mcpServers": {"manipulation": mcp_server_spec}}, indent=2) + "\n")
        (workspace / "policy_instructions.md").write_text(instructions + "\n")

        allowed = [f"mcp__manipulation__{name}" for name in tool_names] if tool_names else ["mcp__manipulation__*"]

        # Note: Relay endpoints compatible with Claude Code validate the native base prompt header;
        # --append-system-prompt injects our full policy instructions while --tools "" disables every
        # built-in Bash/Read/Edit/Write/Web/Agent tool, leaving ONLY the allowed manipulation MCP tools.
        policy_banner = (
            "IMPORTANT EVALUATION BOUNDARY: You are an embodied robot policy under evaluation in a simulator, "
            "NOT a software coding assistant. Built-in filesystem, shell, web, and subagent tools are disabled. "
            "Use ONLY the `mcp__manipulation__*` tools.\n\n" + instructions
        )

        command = [
            executable,
            "-p",
            "--output-format", "stream-json",
            "--verbose",
            "--tools", "",
            "--strict-mcp-config",
            "--mcp-config", str(mcp_path),
            "--allowedTools", ",".join(allowed),
            "--disable-slash-commands",
            "--setting-sources", "user",
            "--append-system-prompt", policy_banner,
            "--model", config.model,
            "--dangerously-skip-permissions",
        ]
        if config.reasoning_effort is not None:
            effort = "max" if config.reasoning_effort in {"xhigh", "max", "ultra"} else config.reasoning_effort
            command += ["--effort", effort]

        return PreparedProject(command, workspace, config.prompt)
