#!/usr/bin/env bash
# Run one Harbor trial of CLM's own agent (clm-minimal) through our gateway.
#
# Why it exists: the reference agent for the CLM cross-checks (step 6). CLM's code is used from the
# pinned submodule (third_party/context-language-models, CC BY-NC 4.0), never copied. Every model
# call goes to the api_base given below, our gateway, which logs it to <its log dir>/<run_id>/calls.jsonl.
# CLM's own FLOPs block is filled with our constants (measure/model_constants.json: n_body = C_token/2;
# model key 27b gives the attention geometry 16 layers x 6144) and the measured model's tokenizer.
#
# Usage: agents/clm/run.sh <label> <task> <context_budget_tokens> [repeat]
#   e.g. agents/clm/run.sh tc1 acl-permissions-inheritance 32000
# Env: GATEWAY (default http://127.0.0.1:8787), MODEL (default qwen3.8:27b), TBLITE_DIR.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
label="$1"; task="$2"; budget="$3"; repeat="${4:-1}"
gateway="${GATEWAY:-http://127.0.0.1:8787}"
model="${MODEL:-qwen3.8:27b}"
tblite="${TBLITE_DIR:-$HOME/econo/OpenThoughts-TBLite}"
arm="clm-b${budget}"
run_id="${label}.${arm}.${task}.r${repeat}"
out="$ROOT/runs/$label/$arm/${task}-r${repeat}"

constants() { "$ROOT/.venv/bin/python" -c "import json; m = json.load(open('$ROOT/measure/model_constants.json')); $1"; }
n_body=$(constants "print(m['models'][m['measured_model']]['C_token'] // 2)")
hf_id=$(constants "print(m['measured_model'])")

# The gateway must be up, and today's budget must still hold a full run (never cut a run off midway).
health=$(curl -sf "$gateway/health") || { echo "the gateway is not running at $gateway"; exit 1; }
left=$(echo "$health" | "$ROOT/.venv/bin/python" -c "import json, sys; h = json.load(sys.stdin); print(h['max_requests_per_day'] - h['requests_today'] - h['max_calls_per_run'])")
[ "$left" -ge 0 ] || { echo "skipped: the daily request budget cannot hold a full run"; exit 1; }

mkdir -p "$out"
cmd=("$ROOT/.venv-clm/bin/clm-harbor" trial start -p "$tblite/$task" -e docker -a clm-minimal -m "openai/$model"
     --agent-kwarg "api_base=$gateway/run/$run_id/v1" --agent-kwarg "context_budget_tokens=$budget"
     --agent-kwarg "cost_metric=flops" --agent-kwarg "flops_model_key=27b" --agent-kwarg "flops_n_body=$n_body"
     --agent-kwarg "flops_tokenizer=$hf_id"
     --agent-timeout-multiplier 4 --trials-dir "$out/harbor" --trial-name "${arm}-${task}-r${repeat}")
printf '%q ' "${cmd[@]}" > "$out/command.txt"; echo >> "$out/command.txt"
echo "{\"run_id\": \"$run_id\", \"calls_log\": \"runs/gateway/$run_id/calls.jsonl\"}" > "$out/run.json"
"${cmd[@]}" > "$out/harbor.out" 2>&1
echo "done: $run_id (trial in $out/harbor)"
