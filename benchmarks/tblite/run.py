"""Run TBLite tasks with our agent (agents/react/agent.py), every model call through gateway/.

Why it exists: one runner for every arm. The arms differ only by EconoContext:
  raw          the agent sends its whole conversation every call
  econo+jev    EconoContext owns the conversation; Jev answers its questions
  econo+prior  the same rules with fixed guesses (no Jev)
Start the gateway first (.venv/bin/python -m gateway.server --provider purdue). It logs every call
to <its log dir>/<run_id>/calls.jsonl, the input of measure/. Each trial writes
runs/<label>/<arm>/<task>-r<n>/summary.json: reward, calls (from that log), exit, seconds, the
owner's report, and Jev fallbacks (questions the fixed guesses answered because Jev failed). No
dollars anywhere.

Run: .venv/bin/python benchmarks/tblite/run.py --label ta1 --arm raw --tasks acl-permissions-inheritance \
       --model qwen3.8:27b --gateway http://127.0.0.1:8787 [--repeats 1] [--workers 1]
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent))

from tasks import FROZEN, TBLITE  # noqa: E402

ECONO = {"raw": "off", "econo+jev": "jev", "econo+prior": "prior"}


def env_value(name: str) -> str | None:
    """A value from .env (the Jev key: the owner calls Jev from Harbor's process)."""
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'") or None
    return None


def jev_fallbacks(session: Path) -> dict:
    """Questions put to the predictor, and how many fell back to the fixed guesses (Jev failed)."""
    if not session.exists():
        return {"asked": 0, "fell_back": 0}
    asked = fell_back = 0
    with sqlite3.connect(session) as db:
        for (answers,) in db.execute("SELECT answers FROM decisions WHERE answers IS NOT NULL"):
            source = (json.loads(answers) or {}).get("source")
            if source:
                asked += 1
                fell_back += source.startswith("prior (")
    return {"asked": asked, "fell_back": fell_back}


def trial(a, log_dir: Path, task: str, repeat: int) -> dict:
    run_id = f"{a.label}.{a.arm}.{task}.r{repeat}"
    out = ROOT / "runs" / a.label / a.arm / f"{task}-r{repeat}"
    out.mkdir(parents=True, exist_ok=True)
    name = f"{a.arm.replace('+', '-')}-{task}-r{repeat}"[:60]
    command = [str(ROOT / ".venv" / "bin" / "harbor"), "trial", "start", "-p", str(TBLITE / task), "-e", "docker",
               "-a", "agents.react.agent:EconoAgent", "-m", f"openai/{a.model}",
               "--agent-kwarg", f"api_base={a.gateway}/run/{run_id}/v1",
               "--agent-kwarg", f"econo={ECONO[a.arm]}", "--agent-kwarg", f"econo_run={run_id}",
               "--agent-timeout-multiplier", str(a.timeout_multiplier),
               "--trials-dir", str(out / "harbor"), "--trial-name", name]
    (out / "command.txt").write_text(" ".join(command) + "\n")
    env = {**os.environ, "PYTHONPATH": f"{ROOT}:{os.environ.get('PYTHONPATH', '')}"}
    if ECONO[a.arm] == "jev" and "TYPESAFE_API_KEY" not in env and env_value("TYPESAFE_API_KEY"):
        env["TYPESAFE_API_KEY"] = env_value("TYPESAFE_API_KEY")
    started = time.time()
    with (out / "harbor.out").open("w") as log:
        code = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, cwd=ROOT).returncode
    return summarize(run_id, out, out / "harbor" / name, log_dir / run_id / "calls.jsonl", code,
                     time.time() - started)


def summarize(run_id: str, out: Path, trial_dir: Path, calls_log: Path, code: int, seconds: float) -> dict:
    result = json.loads((trial_dir / "result.json").read_text()) if (trial_dir / "result.json").exists() else {}
    trajectory = trial_dir / "agent" / "trajectory.json"
    traj = json.loads(trajectory.read_text()) if trajectory.exists() else {}
    rows = [json.loads(line) for line in calls_log.read_text().splitlines()] if calls_log.exists() else []
    calls = [r for r in rows if r.get("call_no")]
    summary = {"run_id": run_id, "exit": code, "seconds": round(seconds),
               "reward": ((result.get("verifier_result") or {}).get("rewards") or {}).get("reward"),
               "exception": (result.get("exception_info") or {}).get("exception_type"),
               "calls": len(calls), "calls_by_kind": dict(Counter(r["kind"] for r in calls)),
               "refused": len(rows) - len(calls), "upstream_retries": sum(r.get("retries") or 0 for r in calls),
               "agent_calls": len(traj.get("calls") or []), "owner": traj.get("owner"),
               "jev_fallbacks": jev_fallbacks(trial_dir / "agent" / "session.sqlite3"),
               "calls_log": str(calls_log.relative_to(ROOT)) if calls_log.is_relative_to(ROOT) else str(calls_log)}
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--label", required=True)
    p.add_argument("--arm", required=True, choices=sorted(ECONO))
    p.add_argument("--tasks", required=True, help="comma-separated task names, or 'frozen' for the 10")
    p.add_argument("--model", required=True, help="the provider's model id, e.g. qwen3.8:27b")
    p.add_argument("--gateway", default="http://127.0.0.1:8787")
    p.add_argument("--repeats", type=int, default=1)
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--timeout-multiplier", type=float, default=4)
    a = p.parse_args()
    with urllib.request.urlopen(f"{a.gateway}/health", timeout=5) as response:   # the gateway must be up
        health = json.load(response)
    log_dir = Path(health["log_dir"])
    tasks = FROZEN if a.tasks == "frozen" else a.tasks.split(",")
    jobs = [(task, r) for r in range(1, a.repeats + 1) for task in tasks]
    print(f"== {a.label} {a.arm}: {len(jobs)} trials, {a.workers} workers, gateway {health['provider']} "
          f"(log {log_dir})", flush=True)
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        for summary in pool.map(lambda job: trial(a, log_dir, *job), jobs):
            print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
