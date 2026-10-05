"""Pass or fail, from the official SWE-bench evaluation harness (Docker).

Why it exists: success is decided by SWE-bench's own harness and nothing else.
What it must never do: substitute a homemade check. If Docker is missing, it stops.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

from econocontext.observation import observe


def docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


def evaluate(predictions: Path, instance_ids: list[str], run_id: str,
             dataset: str = "SWE-bench/SWE-bench_Verified", workdir: Path | None = None,
             recorder=None) -> dict:
    """Run swebench 5.0.2 on `predictions` (JSONL). Each call needs a fresh run_id: the
    harness caches results by run_id + instance_id."""
    if not docker_available():
        raise SystemExit("Docker is not available: the official SWE-bench evaluation cannot run. "
                         "Start Docker and retry; no substitute check is used.")
    cwd = workdir or predictions.parent
    cmd = [sys.executable, "-m", "swebench.harness.run_evaluation", "--dataset_name", dataset,
           "--predictions_path", str(predictions.resolve()), "--instance_ids", *instance_ids,
           "--max_workers", "1", "--run_id", run_id]
    done = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    observe(recorder, "artifact", {"name": "evaluation_process", "content": {
            "command": cmd, "returncode": done.returncode, "stdout": done.stdout,
            "stderr": done.stderr}}, source="evaluator")
    reports = sorted(Path(cwd).glob(f"*.{run_id}.json"))
    if done.returncode != 0 or not reports:
        raise RuntimeError(f"swebench evaluation failed ({done.returncode}):\n{done.stdout[-2000:]}"
                           f"\n{done.stderr[-2000:]}")
    report = json.loads(reports[-1].read_text())
    resolved = set(report.get("resolved_ids", []))
    return {"report_path": str(reports[-1]), "resolved": sorted(resolved),
            "per_instance": {i: i in resolved for i in instance_ids}, "raw": report}
