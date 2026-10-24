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
BCP_SEARCH_OUTPUT="${BCP_SEARCH_OUTPUT:-$BCP_ROOT/full-search/problem-741-agent-topics-v1}"
if [[ ! -f "$BCP_ROOT/corpus-fts.sqlite" ]]; then
  .venv/bin/python -m benchmarks.bcp.doc_labeling.search_pilot \
    --root "$BCP_ROOT" --output "$BCP_SEARCH_OUTPUT" --index-only
fi
BCP_RUN_PREFIX=()
if command -v caffeinate >/dev/null 2>&1; then BCP_RUN_PREFIX=(caffeinate -i); fi
exec "${BCP_RUN_PREFIX[@]}" .venv/bin/python -m benchmarks.bcp.doc_labeling.search_pilot \
  --root "$BCP_ROOT" --output "$BCP_SEARCH_OUTPUT" "$@"
