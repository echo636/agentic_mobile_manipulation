"""Codex command preparation, preserving the existing evaluated CLI configuration."""
import json
import os
from pathlib import Path
import shutil
import re
import tomllib
from ipaddress import ip_address, AddressValueError
from urllib.parse import urlsplit

from .base import ClientAdapter
from .types import PreparedProject
from ..deadline import tool_wait_seconds


class CodexAdapter(ClientAdapter):
    name = "codex"

    def prepare_project(self, config, instructions, tool_names):
        workspace = config.output / "empty_workspace"
        workspace.mkdir()
        command = ["codex", "exec", "--ignore-user-config", "--skip-git-repo-check", "--json",
                   "--sandbox", "read-only", "--model", config.model, "--cd", str(workspace.resolve()),
                   "-c", 'approval_policy="never"', "-c", 'web_search="disabled"',
                   "-c", "project_doc_max_bytes=0", "-c", 'model_reasoning_summary="auto"',
                   "-c", "mcp_optional_startup_grace_ms=0",
                   "-c", "developer_instructions=" + json.dumps(instructions)]
        if config.reasoning_effort is not None:
            command += ['-c', 'model_reasoning_effort=' + json.dumps(config.reasoning_effort)]
        if config.model_provider_profile is not None:
            profile = config.model_provider_profile
            if not re.fullmatch(r'[A-Za-z0-9_-]+', profile):
                raise ValueError('Invalid model provider profile')
            root = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
            data = tomllib.loads((root / 'config.toml').read_text())
            provider = data.get('model_providers', {}).get(profile)
            if not isinstance(provider, dict) or not all(key in provider for key in
                ('name', 'base_url', 'requires_openai_auth', 'wire_api')):
                raise ValueError('Codex model provider profile is incomplete')
            endpoint = urlsplit(provider['base_url'])
            try:
                private_http = endpoint.scheme == 'http' and ip_address(endpoint.hostname).is_private
            except (AddressValueError, TypeError):
                private_http = False
            if not (endpoint.scheme == 'https' or private_http) or not endpoint.netloc or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
                raise ValueError('Provider endpoint must be HTTPS or private-network HTTP without embedded credentials')
            command += ['-c', 'model_provider=' + json.dumps(profile)]
            for key in ('name', 'base_url', 'requires_openai_auth', 'wire_api'):
                command += ['-c', f'model_providers.{profile}.{key}=' + json.dumps(provider[key])]
        native_sessions = None
        storage = None
        prefix = []
        if config.isolate_client_storage:
            wrapper = shutil.which("bwrap")
            if not wrapper:
                raise RuntimeError("Client storage isolation requires bubblewrap")
            native_sessions = (config.output / "native_sessions").resolve()
            native_sessions.mkdir()
            client_root = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
            session_mount = client_root / "sessions"
            if not session_mount.is_dir():
                raise RuntimeError("Expected existing native sessions directory")
            local_root = os.environ.get("MAS_CLIENT_STATE_ROOT")
            if not local_root:
                raise RuntimeError("MAS_CLIENT_STATE_ROOT must name project-local writable storage")
            state = Path(local_root).resolve() / config.output.name
            state.mkdir(parents=True, exist_ok=False)
            command += ["-c", "sqlite_home=" + json.dumps(str(state)),
                        "-c", "log_dir=" + json.dumps(str((config.output / "native_logs").resolve()))]
            prefix = [wrapper, "--die-with-parent", "--bind", "/", "/", "--dev-bind", "/dev", "/dev",
                      "--proc", "/proc", "--bind", str(native_sessions), str(session_mount)]
            command = [*prefix, "--", *command]
            storage = {"sessions": str(native_sessions), "sqlite": str(state),
                       "auth_copied": False, "home_changed": False}
        for feature in ("shell_tool", "unified_exec", "plugins", "apps", "hooks", "view_image", "multi_agent",
                        "browser_use", "computer_use", "image_generation"):
            command += ["--disable", feature]
        server = {"command": config.mcp_command, "args": config.mcp_args, "enabled_tools": tool_names,
                  "startup_timeout_sec": 60, "required": True,
                  "tool_timeout_sec": tool_wait_seconds(config.timeout), "default_tools_approval_mode": "approve"}
        for key, value in server.items():
            command += ["-c", f"mcp_servers.manipulation.{key}=" + json.dumps(value)]
        command += ["-"]
        return PreparedProject(command, None, config.prompt, native_sessions=native_sessions,
                               client_storage=storage, mcp_prefix=prefix)
