#!/usr/bin/env bash
set -euo pipefail
MAS_REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source "$MAS_REPO/runtime/env.sh"
exec "${MAS_PYTHON:-python}" "$MAS_REPO/runtime/robot_launcher.py" "$@"
