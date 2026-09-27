#!/usr/bin/env bash
# The same instances with EconoContext's decision middleware installed.
# Pass --mode observe (default) or --mode autopilot.
# Add --jev to let the planner ask Jev (econocontext/planner/jev_planner.py) instead
# of the fixed guess. Without --jev nothing about Jev runs.
#   ./scripts/run_econo.sh --label pj1 --set dev --mode observe          # no Jev
#   ./scripts/run_econo.sh --label pj1 --set dev --mode observe --jev    # with Jev
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
exec .venv/bin/python -m hosts.swebench_deepagents.run --arm econo "$@"
