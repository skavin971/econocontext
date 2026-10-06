"""Run one TBLite task in one arm with Claude Code (Sonnet 5), and archive everything.

Why it exists: the v2 experiment. Arms differ only by EconoContext (same Harbor agent and
driver; the baseline has no hooks):
  baseline     no EconoContext
  econo+jev    hooks on, Jev answers the rules' questions
  econo+prior  hooks on, fixed guesses (no Jev)
Needs: the gateway (python -m omnigent_layer.gateway, Anthropic route = key pass-through and
spend cap), the hook service (python -m econocontext.service), Docker.

Run: .venv/bin/python benchmarks/tblite/run.py --label v2a --arm baseline --task acl-permissions-inheritance
     .venv/bin/python benchmarks/tblite/run.py --label v2a --arm econo+jev --task acl-permissions-inheritance
     [--force "4 arrival,7 compact"]   spike 1b: apply those rules whenever they apply
Writes runs/<label>/<arm>/<task>/: summary.json, harbor trial dir, session database.
"""

import argparse
import glob
import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "omnigent_layer" / "src"))

from econocontext.transcript import cost_usd, usage_totals  # noqa: E402

TBLITE = Path(os.environ.get("TBLITE_DIR", Path.home() / "econo" / "OpenThoughts-TBLite"))
FROZEN = ["api-endpoint-permission-canonicalizer", "sales-data-csv-analysis", "acl-permissions-inheritance",
          "maven-slf4j-conflict", "chained-forensic-extraction_20260101_011957", "pandas-etl",
          "malicious-package-forensics", "bandit-delayed-feedback", "anomaly-detection-ranking",
          "scan-linux-persistence-artifacts"]  # EconoCLM seed 20261003, oracle health-checked
MODEL = "claude-sonnet-5"
SERVICE = "http://127.0.0.1:8790"
RATES = yaml.safe_load((ROOT / "config" / "billing_rates.yaml").read_text())["anthropic"][MODEL]["periods"][0]["tiers"][0]


def post(url: str, data: dict) -> dict:
    request = urllib.request.Request(url, data=json.dumps(data).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--label", required=True)
    p.add_argument("--arm", required=True, choices=["baseline", "econo+jev", "econo+prior"])
    p.add_argument("--task", required=True, help=f"a TBLite task; frozen set: {', '.join(FROZEN)}")
    p.add_argument("--task-dir", help="a task directory outside TBLite (e.g. the all-nine spike task)")
    p.add_argument("--force", default="", help="comma-separated rule names to force (spike 1b)")
    p.add_argument("--timeout-multiplier", default="2")
    a = p.parse_args()

    from omnigent_layer import register_run
    run = f"{a.label}:{a.arm}:{a.task}"
    out = ROOT / "runs" / a.label / a.arm / a.task
    out.mkdir(parents=True, exist_ok=True)
    trials = out / "harbor"
    trial_name = f"{a.arm.replace('+', '-')}-{a.task}"[:60]
    # The gateway only passes Claude Code through to Anthropic with the key, prices each call,
    # and enforces the per-run and total dollar caps (host must end in ":claude-code").
    register_run(run, "baseline", "observe", a.task, host="tblite:claude-code",
                 overrides={"model": {"provider": "anthropic", "name": MODEL},
                            "limits": {"per_instance_budget_usd": 1.50}})
    hooks = a.arm != "baseline"
    if hooks:
        post(f"{SERVICE}/runs", {"run": run, "predictor": "jev" if a.arm == "econo+jev" else "prior",
                                 "force": [r.strip() for r in a.force.split(",") if r.strip()],
                                 "sessions_dir": str(out / "sessions"),
                                 "path_map": {"/logs/agent": str(trials / trial_name / "agent")}})
    task_dir = Path(a.task_dir) if a.task_dir else TBLITE / a.task
    command = [str(ROOT / ".venv" / "bin" / "harbor"), "trial", "start", "-p", str(task_dir), "-e", "docker",
               "-a", "adapters.harbor.claude_code_econo:ClaudeCodeEcono", "-m", MODEL,
               "--agent-kwarg", f"econo_run={run if hooks else 'none'}",
               "--agent-timeout-multiplier", a.timeout_multiplier,
               "--trials-dir", str(trials), "--trial-name", trial_name]
    env = {**os.environ, "PYTHONPATH": f"{ROOT}:{os.environ.get('PYTHONPATH', '')}",
           "ANTHROPIC_BASE_URL": f"http://host.docker.internal:8787/run/{run}/anthropic",
           "ANTHROPIC_API_KEY": "econo-placeholder"}  # the gateway puts the real key in
    (out / "command.txt").write_text(" ".join(command) + "\n")
    print(f"== {run}", flush=True)
    started = time.time()
    with (out / "harbor.log").open("w") as log:
        code = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, cwd=ROOT).returncode
    print(json.dumps(summarize(run, out, trials / trial_name, code, time.time() - started), indent=2))


def summarize(run: str, out: Path, trial: Path, code: int, seconds: float) -> dict:
    result = json.loads((trial / "result.json").read_text()) if (trial / "result.json").exists() else {}
    rewards = (result.get("verifier_result") or {}).get("rewards") or {}
    transcripts = glob.glob(str(trial / "agent" / "sessions" / "projects" / "**" / "*.jsonl"), recursive=True)
    totals = usage_totals(transcripts)
    summary = {"run": run, "exit": code, "seconds": round(seconds), "reward": rewards.get("reward"),
               "exception": (result.get("exception_info") or {}).get("exception_type"),
               "usage": totals, "run_cost_usd": round(cost_usd(totals, RATES), 4)}
    gateway = ROOT / "data" / "econocontext.sqlite3"
    with sqlite3.connect(gateway) as db:
        summary["gateway_cost_usd"] = db.execute("SELECT round(sum(cost_usd), 4) FROM outcomes WHERE run_id=?",
                                                 (run,)).fetchone()[0]
    sessions = sorted((out / "sessions").glob("*.sqlite3"))
    rules, jev_in, jev_calls = {}, 0, 0
    for path in sessions:
        with sqlite3.connect(path) as db:
            for rule, action, usage in db.execute("SELECT rule, action, jev_usage FROM decisions"):
                rules[f"{rule}: {action}"] = rules.get(f"{rule}: {action}", 0) + 1
                if usage:
                    jev_calls += 1
                    jev_in += json.loads(usage).get("input_tokens", 0)
    summary["decisions"] = rules
    summary["jev"] = {"calls": jev_calls, "input_tokens": jev_in,
                      "cost_usd_at_0.042_per_mtok": round(jev_in * 0.042 / 1e6, 5)}  # kept apart from run cost
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    main()
