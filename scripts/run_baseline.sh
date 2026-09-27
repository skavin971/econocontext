#!/usr/bin/env bash
# Stock Deep Agents on the chosen instances, measured (no EconoContext decisions).
# Credentials come from .env into the environment; they are never printed.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
exec .venv/bin/python -m hosts.swebench_deepagents.run --arm baseline "$@"
