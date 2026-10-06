"""Run TBLite tasks with our own Gemini agent (adapters/harbor/econo_agent.py), in one arm.

Why it exists: the full-control track. Arms differ only by EconoContext:
  raw          the agent sends its whole conversation every call
  econo+jev    EconoContext owns the conversation; Jev answers its questions
  econo+prior  the same rules with fixed guesses (no Jev)
The gateway is a pure pass-through (runs registered as "baseline") that records each call's
usage and cost. It has no dollar cap on this route, so this runner enforces the budget: before
each trial it sums the cost of every run on host "econo-agent:gemini" and stops at --budget.
Needs the gateway with ECONO_MAX_CALLS_PER_RUN=70 and a raised ECONO_MAX_INPUT_TOKENS_PER_DAY,
the hook-free EconoContext core (in process), and Docker.

Run: .venv/bin/python benchmarks/tblite/run_gemini.py --label g1 --arm raw --tasks sales-data-csv-analysis
     .venv/bin/python benchmarks/tblite/run_gemini.py --label g2 --arm econo+jev --tasks frozen --repeats 1 --workers 2
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "omnigent_layer" / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from run import FROZEN, TBLITE  # noqa: E402

HOST = "econo-agent:gemini"
MODEL = "openai/google/gemini-3.6-flash"
DB = ROOT / "data" / "econocontext.sqlite3"
ECONO = {"raw": "off", "econo+jev": "jev", "econo+prior": "prior"}
lock = threading.Lock()


def spent() -> float:
    with sqlite3.connect(DB) as db:
        return db.execute("SELECT COALESCE(SUM(o.cost_usd), 0) FROM outcomes o JOIN runs r ON r.run_id = o.run_id "
                          "WHERE r.host = ?", (HOST,)).fetchone()[0]


def trial(a, task: str, repeat: int) -> dict:
    from omnigent_layer import register_run
    run = f"{a.label}:{a.arm}:{task}:r{repeat}"
    out = ROOT / "runs" / a.label / a.arm / f"{task}-r{repeat}"
    with lock:  # budget check and registration, one trial at a time
        used = spent()
        if used >= a.budget:
            return {"run": run, "skipped": f"budget: ${used:.2f} of ${a.budget:.2f} spent"}
        out.mkdir(parents=True, exist_ok=True)
        register_run(run, "baseline", "observe", task, host=HOST, overrides={"limits": {"max_model_calls": 70}})
    task_dir = Path(a.task_dir) if a.task_dir else TBLITE / task
    name = f"{a.arm.replace('+', '-')}-{task}-r{repeat}"[:60]
    command = [str(ROOT / ".venv" / "bin" / "harbor"), "trial", "start", "-p", str(task_dir), "-e", "docker",
               "-a", "adapters.harbor.econo_agent:EconoAgent", "-m", MODEL,
               "--agent-kwarg", f"api_base=http://127.0.0.1:8787/run/{run}/v1",
               "--agent-kwarg", f"econo={ECONO[a.arm]}", "--agent-kwarg", f"econo_run={run}",
               "--agent-timeout-multiplier", str(a.timeout_multiplier),
               "--trials-dir", str(out / "harbor"), "--trial-name", name]
    if a.force:
        command += ["--agent-kwarg", f"force={json.dumps(a.force.split(','))}"]
    (out / "command.txt").write_text(" ".join(command) + "\n")
    env = {**os.environ, "PYTHONPATH": f"{ROOT}:{os.environ.get('PYTHONPATH', '')}"}
    # The owner runs inside Harbor's process and asks Jev: give it the key from .env, as the gateway
    # reads its keys (spike gspike ran with no key and every Jev question fell back to the prior).
    if a.arm == "econo+jev" and "TYPESAFE_API_KEY" not in env:
        from omnigent_layer.gateway_common import env as dotenv
        if dotenv("TYPESAFE_API_KEY"):
            env["TYPESAFE_API_KEY"] = dotenv("TYPESAFE_API_KEY")
    started = time.time()
    with (out / "harbor.log").open("w") as log:
        code = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, cwd=ROOT).returncode
    return summarize(run, out, out / "harbor" / name, code, time.time() - started)


def summarize(run: str, out: Path, trial_dir: Path, code: int, seconds: float) -> dict:
    result = json.loads((trial_dir / "result.json").read_text()) if (trial_dir / "result.json").exists() else {}
    trajectory = trial_dir / "agent" / "trajectory.json"
    traj = json.loads(trajectory.read_text()) if trajectory.exists() else {}
    with sqlite3.connect(DB) as db:
        row = db.execute("SELECT COUNT(*), COALESCE(SUM(cost_usd), 0), COALESCE(SUM(uncached_input), 0), "
                         "COALESCE(SUM(cache_read), 0), COALESCE(SUM(output), 0) FROM outcomes WHERE run_id=?",
                         (run,)).fetchone()
    summary = {"run": run, "exit": code, "seconds": round(seconds),
               "reward": ((result.get("verifier_result") or {}).get("rewards") or {}).get("reward"),
               "exception": (result.get("exception_info") or {}).get("exception_type"),
               "calls": row[0], "run_cost_usd": round(row[1], 4),
               "tokens": {"uncached": row[2], "cached": row[3], "output": row[4]},
               "agent_calls": len(traj.get("calls") or []), "owner": traj.get("owner")}
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--label", required=True)
    p.add_argument("--arm", required=True, choices=sorted(ECONO))
    p.add_argument("--tasks", required=True, help="comma-separated task names, or 'frozen' for the 10")
    p.add_argument("--task-dir", help="a task directory outside TBLite (one task only)")
    p.add_argument("--repeats", type=int, default=1)
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--budget", type=float, default=12.0, help="stop when Gemini spend on this host reaches it")
    p.add_argument("--force", default="", help="comma-separated rule names to force (spike only)")
    p.add_argument("--timeout-multiplier", type=float, default=4)
    a = p.parse_args()
    tasks = FROZEN if a.tasks == "frozen" else a.tasks.split(",")
    jobs = [(task, r) for r in range(1, a.repeats + 1) for task in tasks]
    print(f"== {a.label} {a.arm}: {len(jobs)} trials, {a.workers} workers, spent so far ${spent():.4f}", flush=True)
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        for summary in pool.map(lambda job: trial(a, *job), jobs):
            print(json.dumps(summary), flush=True)
    print(f"== done; Gemini spend on {HOST}: ${spent():.4f}", flush=True)


if __name__ == "__main__":
    main()
