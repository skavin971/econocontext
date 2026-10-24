#!/usr/bin/env bash
set -euo pipefail
BCP_RUN_ROOT="$(cd "$(dirname "$0")" && pwd -P)"
cd "$BCP_RUN_ROOT/../.."
set -a
if [[ -f .env ]]; then source .env; fi
BCP_PILOT_ENV="${BCP_PILOT_ENV:-$BCP_RUN_ROOT/pilot.env}"
if [[ ! -f "$BCP_PILOT_ENV" ]]; then BCP_PILOT_ENV="$BCP_RUN_ROOT/pilot.env.example"; fi
source "$BCP_PILOT_ENV"
set +a
exec .venv/bin/python "$BCP_RUN_ROOT/run_pilot.py" "$@"
