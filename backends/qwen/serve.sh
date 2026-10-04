#!/usr/bin/env bash
# Serve Qwen3.6-27B in bf16 on one H100 with vLLM, for econoclm/bench/tblite/run_qwen.sh.
#
#   bash backends/qwen/serve.sh          # run inside tmux; leave it running
#
# Pinned: the vLLM version and the model revision (Hugging Face commit). Prefix caching
# is on, and every response reports prompt_tokens_details.cached_tokens, which CLM's
# flops_metrics needs for its "server_usage_cached" FLOPs. Server settings are written
# to ~/.econoclm/qwen_server.json; run_qwen.py copies them into every run's report, so
# later arms can be checked against the same setup. Keep these values identical for
# every arm: a server restart is fine, a change is not.
set -euo pipefail

VLLM_VERSION=0.30.0                                    # PyPI, released 2026-09-22
MODEL=Qwen/Qwen3.6-27B
REVISION=6a9e13bd6fc8f0983b9b99948120bc37f49c13e9      # HF commit (lastModified 2026-04-24)
SERVED_NAME=qwen36-27b                                 # the arms use model openai/qwen36-27b
PORT=${PORT:-8000}
VENV=${VLLM_VENV:-$HOME/econo/.venv-vllm}              # vLLM gets its own venv

FLAGS=(
  --revision "$REVISION" --tokenizer-revision "$REVISION"
  --served-model-name "$SERVED_NAME"
  --dtype bfloat16 --tensor-parallel-size 1
  --max-model-len 65536 --gpu-memory-utilization 0.92
  --enable-prefix-caching --enable-prompt-tokens-details
  --enable-auto-tool-choice --tool-call-parser qwen3_coder --reasoning-parser qwen3
  --host 127.0.0.1 --port "$PORT"
)

if [ ! -x "$VENV/bin/vllm" ]; then
  echo "installing vllm==$VLLM_VERSION into $VENV"
  python3.12 -m venv "$VENV"
  "$VENV/bin/pip" install -q -U pip
  "$VENV/bin/pip" install -q "vllm==$VLLM_VERSION"
fi
installed=$("$VENV/bin/python" -c 'import vllm; print(vllm.__version__)')
if [ "$installed" != "$VLLM_VERSION" ]; then
  echo "ERROR: $VENV has vllm $installed, expected $VLLM_VERSION (delete $VENV and rerun)" >&2
  exit 1
fi

mkdir -p "$HOME/.econoclm"
"$VENV/bin/python" - "$VLLM_VERSION" "$MODEL" "$REVISION" "$SERVED_NAME" "$PORT" "${FLAGS[*]}" <<'PY'
import datetime, json, os, subprocess, sys
version, model, revision, served, port, flags = sys.argv[1:]
def sh(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception as exc:
        return f"unavailable: {exc}"
info = {"vllm_version": version, "model": model, "revision": revision, "served_model_name": served,
        "port": int(port), "flags": flags, "started": datetime.datetime.now().isoformat(timespec="seconds"),
        "gpu": sh("nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader"),
        "cuda": sh("nvidia-smi | grep -o 'CUDA Version: [0-9.]*'"),
        "torch": sh(f"{sys.executable} -c 'import torch; print(torch.__version__, torch.version.cuda)'")}
path = os.path.expanduser("~/.econoclm/qwen_server.json")
open(path, "w").write(json.dumps(info, indent=2) + "\n")
print(f"server settings -> {path}")
PY

echo "serving $MODEL@${REVISION:0:12} as $SERVED_NAME on 127.0.0.1:$PORT (vllm $VLLM_VERSION)"
exec "$VENV/bin/vllm" serve "$MODEL" "${FLAGS[@]}"
