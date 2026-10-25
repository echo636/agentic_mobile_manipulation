#!/usr/bin/env bash
set -euo pipefail
# Project-specific runtime; no dependency on either source author's Python packages.
MAS_SHARED=/mnt/nas/home--xujingyi/agentic_mobile_manip
MAS_LOCAL=/mnt/data2/home/xujingyi/agentic_mobile_manip
MAS_REPO="$MAS_SHARED/repos/manipulation-agentic-system"
export OMNI_KIT_ACCEPT_EULA=YES OMNIGIBSON_HEADLESS=1
export OMNIGIBSON_GPU_ID="${MAS_GPU:-3}"
export MAS_GPU_UUID="$(nvidia-smi -i "$OMNIGIBSON_GPU_ID" --query-gpu=uuid --format=csv,noheader)"
export OMNIGIBSON_DATA_PATH="$MAS_LOCAL/data/omnigibson"
export OMNIGIBSON_APPDATA_PATH="$MAS_LOCAL/cache/mas-omnigibson"
export PYTHONPATH="$MAS_REPO/src"
export TMPDIR="$MAS_LOCAL/tmp" XDG_CACHE_HOME="$MAS_LOCAL/cache"
export CUDA_HOME=/usr/local/cuda-12.8
export PATH="$MAS_LOCAL/envs/behavior/bin:$CUDA_HOME/bin:$PATH"
export TORCH_EXTENSIONS_DIR="$MAS_LOCAL/cache/torch-extensions" CUDA_CACHE_PATH="$MAS_LOCAL/cache/cuda"
export SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
export REQUESTS_CA_BUNDLE="$SSL_CERT_FILE"
export OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 MAX_JOBS=4 PYTHONHASHSEED=0
export NO_PROXY=127.0.0.1,localhost
export no_proxy="$NO_PROXY"
exec "$MAS_LOCAL/envs/behavior/bin/python" -u -m manipulation_agent.vision_cli "$@"
