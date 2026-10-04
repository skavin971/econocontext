#!/usr/bin/env bash
# One command for the Qwen runs (see econoclm/RUN_QWEN.md). Run inside tmux.
#   bash econoclm/bench/tblite/run_qwen.sh --arms clm --reps 3
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$ROOT"
# shellcheck disable=SC1091
source "$ROOT/.venv-econoclm/bin/activate"
exec python -m econoclm.bench.tblite.run_qwen "$@"
