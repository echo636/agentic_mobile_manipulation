"""Codex command preparation, preserving the existing evaluated CLI configuration."""
import json
import os
from pathlib import Path
import shutil
import re
import subprocess
import tomllib
from dataclasses import replace
from uuid import UUID
from ipaddress import ip_address, AddressValueError
from urllib.parse import urlsplit

from .base import ClientAdapter
from .types import PreparedProject
from ..runtime.deadline import tool_wait_seconds

# codex_context="minimal": remove Codex-owned prompt blocks and native tools, verified
# against the request body of codex-cli 0.166.0. The org-managed SessionStart hook in
# /etc/codex/requirements.toml is policy and remains, as do the three MCP resource tools.
MINIMAL_CONTEXT_ARGS = [
    "-c", "include_permissions_instructions=false", "-c", "include_environment_context=false",
    "-c", "include_collaboration_mode_instructions=false", "-c", "skills.include_instructions=false",
    "-c", "tools.experimental_request_user_input.enabled=false",
    "--disable", "sleep_tool", "--disable", "code_mode_host", "--disable", "goals"]
# Catalog metadata that adds the Codex base prompt, code-mode exec, multi-agent tools,
# apply_patch, tool_search deferral and a duplicated responses-lite prompt.
MINIMAL_CATALOG_OVERRIDES = {"apply_patch_tool_type": None, "use_responses_lite": False,
                             "supports_search_tool": False, "experimental_supported_tools": []}
MINIMAL_CATALOG_REMOVED = ("tool_mode", "multi_agent_version", "multi_agent_reasoning_effort", "model_messages")
UNLISTED_MODEL_TEMPLATE = "gpt-6-luna"


def bundled_model_catalog():
    output = subprocess.run(["codex", "debug", "models", "--bundled"], capture_output=True, text=True,
                            check=True, timeout=60).stdout
    return json.loads(output)


def minimal_model_catalog(catalog, model, instructions):
    """Keep each model's limits; make the experiment policy the only base instructions."""
    models = catalog["models"]
    for entry in models:
        for key in MINIMAL_CATALOG_REMOVED:
            entry.pop(key, None)
        entry.update(MINIMAL_CATALOG_OVERRIDES, base_instructions=instructions)
    source = "bundled"
    if not any(entry["slug"] == model for entry in models):
        template = next((entry for entry in models if entry["slug"] == UNLISTED_MODEL_TEMPLATE), None)
        if template is None:
            raise ValueError("Bundled Codex catalog lacks the template for unlisted models")
        models.append({**template, "slug": model, "display_name": model})
        source = "cloned_from:" + UNLISTED_MODEL_TEMPLATE
    return catalog, source


class CodexAdapter(ClientAdapter):
    name = "codex"

    def prepare_fork(self, project, thread_id, prompt):
        """Keep native conversation history and the live MCP endpoint in a new thread."""
        continued = self.prepare_resume(project, thread_id, prompt)
        offset = len(continued.mcp_prefix) + 1 if continued.mcp_prefix else 0
        argv = list(continued.argv)
        assert argv[offset:offset + 3] == ['codex', 'exec', 'resume']
        argv[offset + 2] = 'fork'
        return replace(continued, argv=argv)

    def prepare_resume(self, project, thread_id, prompt):
        """Resume one recorded Codex thread against the same MCP episode."""
        UUID(thread_id)
        prefix = project.mcp_prefix
        argv = project.argv
        if prefix:
            if argv[:len(prefix)+1] != [*prefix, '--']:
                raise ValueError('Codex resume namespace prefix differs from original project')
            argv = argv[len(prefix)+1:]
        if argv[:2] != ['codex', 'exec']:
            raise ValueError('Codex resume requires an exec project')
        command = ['codex', 'exec', 'resume']
        workspace = project.cwd
        args = argv[2:]
        index = 0
        while index < len(args):
            flag = args[index]
            if flag == '--cd':
                workspace = Path(args[index + 1])
                index += 2
            elif flag == '--sandbox':
                command += ['-c', 'sandbox_mode=' + json.dumps(args[index + 1])]
                index += 2
            elif flag == '-':
                index += 1
            else:
                command.append(flag)
                index += 1
        command += [thread_id, '-']
        if prefix:
            command = [*prefix, '--', *command]
        return PreparedProject(command, workspace, prompt, env_overlay=project.env_overlay,
            native_sessions=project.native_sessions, client_storage=project.client_storage,
            mcp_prefix=prefix)

    def prepare_project(self, config, instructions, tool_names):
        workspace = config.output / "empty_workspace"
        workspace.mkdir()
        command = ["codex", "exec", "--ignore-user-config", "--skip-git-repo-check", "--json",
                   "--sandbox", "read-only", "--model", config.model, "--cd", str(workspace.resolve()),
                   "-c", 'approval_policy="never"', "-c", 'web_search="disabled"',
                   "-c", "project_doc_max_bytes=0", "-c", 'model_reasoning_summary="auto"',
                   "-c", "mcp_optional_startup_grace_ms=0",
                   # Keep RGB delivery in Codex's native MCP handler, even when
                   # the model catalog selects code_mode_only. Model-authored
                   # JS can drop images before forwarding the tool result.
                   "-c", 'features.code_mode.direct_only_tool_namespaces=["mcp__manipulation"]']
        if config.codex_context == "minimal":
            catalog, model_entry = minimal_model_catalog(bundled_model_catalog(), config.model, instructions)
            catalog_path = (config.output / "codex_model_catalog.json").resolve()
            catalog_path.write_text(json.dumps(catalog, ensure_ascii=False))
            (config.output / "codex_context.json").write_text(json.dumps({
                "mode": "minimal", "model_catalog": str(catalog_path), "model_entry": model_entry,
                "removed_catalog_fields": list(MINIMAL_CATALOG_REMOVED),
                "catalog_overrides": MINIMAL_CATALOG_OVERRIDES, "codex_args": MINIMAL_CONTEXT_ARGS,
                "retained": ["org-managed SessionStart hook instructions (/etc/codex)",
                             "list_mcp_resources", "list_mcp_resource_templates", "read_mcp_resource"],
            }, indent=2) + "\n")
            command += [*MINIMAL_CONTEXT_ARGS, "-c", "model_catalog_json=" + json.dumps(str(catalog_path))]
        else:
            command += ["-c", "developer_instructions=" + json.dumps(instructions)]
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
                ('name', 'base_url', 'wire_api')):
                raise ValueError('Codex model provider profile is incomplete')
            provider = dict(provider)
            provider.setdefault('requires_openai_auth', False)
            env_key = provider.get('env_key')
            if env_key is not None:
                if not isinstance(env_key, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', env_key):
                    raise ValueError('Invalid provider credential environment name')
                if not os.environ.get(env_key):
                    raise ValueError('Provider credential environment absent: ' + env_key)
                # An explicitly selected API-key provider must never fall back
                # to the user's ChatGPT subscription login.
                provider['requires_openai_auth'] = False
                # The CLI also loads its shared credential cache for background
                # work. A revoked subscription refresh must not abort an
                # independent, explicitly configured API-key episode. Keep
                # credentials process-local; never edit the user's auth file.
                command += ['-c', 'cli_auth_credentials_store="ephemeral"']
            endpoint = urlsplit(provider['base_url'])
            try:
                private_http = endpoint.scheme == 'http' and ip_address(endpoint.hostname).is_private
            except (AddressValueError, TypeError):
                private_http = False
            if not (endpoint.scheme == 'https' or private_http) or not endpoint.netloc or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
                raise ValueError('Provider endpoint must be HTTPS or private-network HTTP without embedded credentials')
            if config.model_provider_gateway is not None:
                gateway = urlsplit(config.model_provider_gateway)
                if (gateway.scheme != 'http' or gateway.hostname not in {'127.0.0.1', '::1'}
                        or gateway.username or gateway.password or gateway.query or gateway.fragment
                        or gateway.path not in {'', '/'}):
                    raise ValueError('Provider gateway must be a loopback HTTP base URL without credentials or path')
                provider['base_url'] = config.model_provider_gateway.rstrip('/')
            command += ['-c', 'model_provider=' + json.dumps(profile)]
            for key in ('name', 'base_url', 'requires_openai_auth', 'wire_api'):
                command += ['-c', f'model_providers.{profile}.{key}=' + json.dumps(provider[key])]
            if env_key is not None:
                command += ['-c', f'model_providers.{profile}.env_key=' + json.dumps(env_key)]
            for key in ('request_max_retries', 'stream_max_retries', 'stream_idle_timeout_ms'):
                if key in provider:
                    if type(provider[key]) is not int or provider[key] < 0:
                        raise ValueError('Provider retry and timeout options must be nonnegative integers')
                    command += ['-c', f'model_providers.{profile}.{key}=' + str(provider[key])]
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
            # bubblewrap cannot bind onto a symlink destination. Mount its real
            # directory so Codex still follows the existing sessions symlink,
            # while writes remain isolated in this episode's private directory.
            session_mount = (client_root / "sessions").resolve()
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
        if tool_names:
            server = {"command": config.mcp_command, "args": config.mcp_args, "enabled_tools": tool_names,
                      "startup_timeout_sec": 60, "required": True,
                      "tool_timeout_sec": tool_wait_seconds(config.timeout), "default_tools_approval_mode": "approve"}
            if config.mcp_tool_exposure == "direct":
                # Code-mode-only model metadata otherwise hides MCP tools behind a model-written exec
                # script, which can stringify image blocks into truncated text.
                server["omit_tools_from"] = ["code_mode"]
            if config.mcp_env_vars:
                server['env_vars'] = list(config.mcp_env_vars)
            for key, value in server.items():
                command += ["-c", f"mcp_servers.manipulation.{key}=" + json.dumps(value)]
            if pythonpath := os.environ.get('PYTHONPATH'):
                command += ["-c", 'mcp_servers.manipulation.env.PYTHONPATH=' + json.dumps(pythonpath)]
        command += ["-"]
        return PreparedProject(command, None, config.prompt, native_sessions=native_sessions,
                               client_storage=storage, mcp_prefix=prefix)
