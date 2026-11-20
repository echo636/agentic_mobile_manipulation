"""Kimi CLI with a custom MCP-only agent (no default shell/file/subagent tools)."""
import json
from .base import ClientAdapter
from .types import PreparedProject


class KimiAdapter(ClientAdapter):
    name = "kimi"
    required_flags = ("--agent-file", "--mcp-config-file", "--work-dir", "--print", "--output-format", "--skills-dir")

    def prepare_project(self, config, instructions, tool_names):
        if config.isolate_client_storage:
            raise ValueError("Kimi storage isolation is not implemented; remove --isolate-client-storage")
        workspace = (config.output / "kimi_project").resolve()
        workspace.mkdir()
        (workspace / "empty_skills").mkdir()
        (workspace / "system.md").write_text(instructions + "\n")
        (workspace / "agent.yaml").write_text(
            "version: 1\nagent:\n  name: manipulation\n  system_prompt_path: ./system.md\n  tools: []\n  subagents: {}\n")
        # An explicit file suppresses Kimi's default global MCP file. The MCP server
        # itself enforces the selected tool profile; no global state is rewritten.
        mcp = {"mcpServers": {"manipulation": {"command": config.mcp_command, "args": config.mcp_args}}}
        (workspace / "mcp.json").write_text(json.dumps(mcp, indent=2) + "\n")
        command = ["kimi", "--model", config.model, "--work-dir", str(workspace),
                   "--agent-file", str(workspace / "agent.yaml"),
                   "--mcp-config-file", str(workspace / "mcp.json"),
                   "--skills-dir", str(workspace / "empty_skills"),
                   "--print", "--output-format", "stream-json"]
        return PreparedProject(command, workspace, config.prompt)
