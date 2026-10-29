"""OpenCode local-MCP adapter. Live RGB delivery requires a separately recorded smoke run."""
import json
from .base import ClientAdapter
from .types import PreparedProject


class OpenCodeAdapter(ClientAdapter):
    name = "opencode"
    required_flags = ("--format", "--model", "--agent", "--dir")
    help_args = ("run", "--help")

    def prepare_project(self, config, instructions, tool_names):
        if config.isolate_client_storage:
            raise ValueError("OpenCode storage isolation is not implemented; remove --isolate-client-storage")
        workspace = (config.output / "opencode_project").resolve()
        workspace.mkdir()
        permission = {"*": "deny", **{"manipulation_" + name: "allow" for name in tool_names}}
        settings = {"$schema": "https://opencode.ai/config.json", "model": config.model,
                    "share": "disabled", "autoupdate": False, "instructions": [],
                    "permission": permission,
                    "agent": {"manipulation": {"mode": "primary", "description": "RGB manipulation policy",
                        "prompt": instructions, "permission": permission}},
                    "mcp": {"manipulation": {"type": "local", "enabled": True,
                            "command": [config.mcp_command, *config.mcp_args]}}}
        (workspace / "opencode.json").write_text(json.dumps(settings, indent=2) + "\n")
        # Highest ordinary config priority; no credential copies or HOME changes.
        env = {"OPENCODE_CONFIG_CONTENT": json.dumps(settings)}
        command = ["opencode", "run", "--format", "json", "--dir", str(workspace),
                   "--model", config.model, "--agent", "manipulation", config.prompt]
        return PreparedProject(command, workspace, None, env_overlay=env)
