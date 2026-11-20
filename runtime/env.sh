#!/usr/bin/env bash
# Source from the repository root after activating your Python/OmniGibson environment.
if [ -n "${BASH_SOURCE:-}" ]; then
  MAS_REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
else
  MAS_REPO="$PWD"
fi
if [ ! -f "$MAS_REPO/pyproject.toml" ] || [ ! -d "$MAS_REPO/src/manipulation_agent" ]; then
  printf '%s\n' 'Source runtime/env.sh from the repository root.' >&2
  return 2 2>/dev/null || exit 2
fi
export MAS_REPO
export MAS_RUNTIME_ROOT="${MAS_RUNTIME_ROOT:-${XDG_DATA_HOME:-$HOME/.local/share}/manipulation-agentic-system}"
export MAS_EXPERIENCE_ROOT="$MAS_RUNTIME_ROOT/experience_skills"
export MAS_PROGRAM_LIBRARY_ROOT="$MAS_RUNTIME_ROOT/skill_library"
export MAS_EXPLORATION_SOCKET_ROOT="${MAS_EXPLORATION_SOCKET_ROOT:-/tmp/mas-exploration-$UID}"
export PYTHONPATH="$MAS_REPO/src${PYTHONPATH:+:$PYTHONPATH}"
export OMNIGIBSON_DATA_PATH="${OMNIGIBSON_DATA_PATH:-$MAS_RUNTIME_ROOT/datasets}"
export OMNIGIBSON_APPDATA_PATH="${OMNIGIBSON_APPDATA_PATH:-$MAS_RUNTIME_ROOT/appdata}"
export OMNIGIBSON_HEADLESS="${OMNIGIBSON_HEADLESS:-1}"
mkdir -p "$MAS_RUNTIME_ROOT" "$OMNIGIBSON_APPDATA_PATH"
# Simulator EULA acceptance, model credentials, proxy and GPU selection are user settings.
