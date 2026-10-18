"""Shared paths and helpers for the PFLOP accuracy step (pflop_accuracy/README.md). Standard library only, so setup.py
can use it before any virtual environment exists."""

import json
import subprocess
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]               # the repository
HERE = Path(__file__).resolve().parent
BCP = Path.home() / "econo" / "bcp"                       # BrowseComp-Plus data, tasks, runs and logs: never in the repo
BCP_REPO = BCP / "BrowseComp-Plus"
BCP_COMMIT = "0469490"                                    # texttron/BrowseComp-Plus, as used in our pilot
DATA = BCP_REPO / "data"
INDEX = BCP_REPO / "indexes" / "bm25"
TASKS = BCP / "tasks"
RUNS = BCP / "runs"
GATEWAY_LOGS = BCP / "gateway"
RESULTS = ROOT / "runs" / "pflop-accuracy"                # numbers only: these tables are committed

PY = ROOT / ".venv" / "bin" / "python"                    # our measurement code and runner
CLM_HARBOR = ROOT / ".venv-clm" / "bin" / "clm-harbor"    # CLM's agent, as released
SEARCH_PY = BCP / ".venv" / "bin" / "python"              # the BM25 search service (pyserini, Java 21)

GATEWAY = "http://127.0.0.1:8787"
SEARCH = "http://127.0.0.1:8790"
VLLM = "http://127.0.0.1:8000/v1"
MODEL = "qwen3.8:27b"                                     # the served name, on Purdue and on our vLLM
HF_MODEL = "Qwen/Qwen3.8-27B-FP8"
LABEL_PREFIX = "bcp-pflop"                                # every run of this step: bcp-pflop-<backend>-<date>
QUESTIONS = 8                                             # the first 8 of benchmarks/bcp/split.json "pilot"


def revision() -> str:
    """The model revision our FLOPs constants were measured on (measure/model_constants.json)."""
    return json.loads((ROOT / "measure" / "model_constants.json").read_text())["models"][HF_MODEL]["revision"]


def get_json(url: str, timeout: float = 5.0):
    """GET a JSON endpoint; None when it does not answer."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.load(response)
    except (OSError, ValueError):
        return None


def run(command: list, cwd: Path = ROOT, check: bool = True, quiet: bool = False) -> int:
    """Run a command, showing it first."""
    print("  $ " + " ".join(str(c) for c in command), flush=True)
    result = subprocess.run([str(c) for c in command], cwd=cwd,
                            stdout=subprocess.DEVNULL if quiet else None, stderr=subprocess.DEVNULL if quiet else None)
    if check and result.returncode:
        raise SystemExit(f"failed (exit {result.returncode}): {' '.join(str(c) for c in command)}")
    return result.returncode


def env_has(name: str) -> bool:
    """Whether .env sets a non-empty value for name (the value is never printed)."""
    env = ROOT / ".env"
    if not env.exists():
        return False
    for line in env.read_text().splitlines():
        if line.startswith(f"{name}=") and line.split("=", 1)[1].strip().strip('"').strip("'"):
            return True
    return False
