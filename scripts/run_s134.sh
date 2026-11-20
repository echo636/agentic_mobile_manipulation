#!/usr/bin/env bash
set -euo pipefail
# Historical entry name, using the current checkout and the caller's environment.
MAS_REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source "$MAS_REPO/runtime/env.sh"
if [ -n "${MAS_GPU:-}" ]; then
  export OMNIGIBSON_GPU_ID="$MAS_GPU"
fi
exec "${MAS_PYTHON:-python}" -u -m manipulation_agent.vision_cli "$@"
