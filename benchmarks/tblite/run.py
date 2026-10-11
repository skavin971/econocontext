"""Run TBLite tasks through gateway/ on one or more arms: our agent's and CLM's.

Why it exists: one runner for every arm, so each arm gets the same tasks, settings, gateway and
records. The arms:
  raw          our agent (agents/react/agent.py); it sends its whole conversation every call
  econo+jev    our agent with EconoContext, Jev answering its questions
  econo+prior  the same rules with fixed guesses (no Jev)
  clm-32k      CLM's own agent (agents/clm/run.sh) at its published setting: context_budget_tokens 32,000
  clm-full     CLM's agent at the real window: 57,344 = Purdue's 65,536 minus max_tokens 8,192
Start the gateway first (.venv/bin/python -m gateway.server --provider purdue). Every call is logged
to <gateway log dir>/<run_id>/calls.jsonl (run id <label>.<arm>.<task>.r<n>), the input of measure/.
Each trial writes runs/<label>/<arm>/<task>-r<n>/summary.json. Several arms run interleaved (for each
repeat, for each task, every arm), so arms run close in time. No dollars anywhere.

Stops cleanly between trials, never mid-trial:
- the daily request budget must hold a full run (the gateway's per-run cap) for this trial and for
  every trial already running, otherwise the run stops (the Gemini runs lost 9 trials to a cap);
- the gateway must answer /health; it is retried for up to 10 minutes;
- on macOS the runner keeps the machine awake (caffeinate) and stops if the battery is at 20% or less
  while discharging (a dead battery would cut trials mid-run);
- with --gates, each finished trial's calls are rebuilt and validated (measure/), and the run stops on:
  3+ invalid calls in a trial; an arm crashing in 2 trials in a row; Jev falling back on more than
  half its questions in a trial.
With --progress, one line per finished trial and every gate decision are appended to that file.

Run: .venv/bin/python benchmarks/tblite/run.py --label tc9 --arm raw,econo+jev,clm-32k,clm-full --tasks frozen \
       --repeats 3 --model qwen3.8:27b --workers 2 --gates --progress runs/tier-a/step9-progress.md
"""

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(ROOT))

from tasks import FROZEN, TBLITE  # noqa: E402

ECONO = {"raw": "off", "econo+jev": "jev", "econo+prior": "prior"}
CLM = {"clm-32k": 32_000, "clm-full": 65_536 - 8_192}
ARMS = list(ECONO) + list(CLM)
BATTERY_STOP = 20                     # percent, while discharging
budget = threading.Lock()             # guards the counters below and the progress file
running = [0]                         # trials started and not finished; each may still need a full run's calls
stop = {"reason": None}               # set by a gate: no new trial starts
crashes: Counter = Counter()          # arm -> crashes in a row
measured = threading.Lock()           # the tokenizer is used by one thread at a time
_measure = {}


def env_value(name: str) -> str | None:
    """A value from .env (the Jev key: the owner calls Jev from Harbor's process)."""
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'") or None
    return None


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def progress(a, line: str) -> None:
    if a.progress:
        with Path(a.progress).open("a") as f:
            f.write(f"- {now()} | {line}\n")
    print(line, flush=True)


def health(gateway: str, wait_s: float = 0) -> dict | None:
    deadline = time.time() + wait_s
    while True:
        try:
            with urllib.request.urlopen(f"{gateway}/health", timeout=5) as response:
                return json.load(response)
        except OSError:
            if time.time() >= deadline:
                return None
            time.sleep(15)


def battery() -> tuple[int, bool] | None:
    """(percent, on battery) on macOS, else None."""
    if sys.platform != "darwin":
        return None
    out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True).stdout
    found = re.search(r"(\d+)%", out)
    return (int(found.group(1)), "Battery Power" in out) if found else None


def jev_fallbacks(session: Path) -> dict:
    """Questions put to the predictor, how many fell back to the fixed guesses, and why (error types)."""
    asked, errors = 0, Counter()
    if session.exists():
        with sqlite3.connect(session) as db:
            for (answers,) in db.execute("SELECT answers FROM decisions WHERE answers IS NOT NULL"):
                source = (json.loads(answers) or {}).get("source")
                if source:
                    asked += 1
                    if source.startswith("prior ("):
                        errors[source[len("prior ("):-1]] += 1
    return {"asked": asked, "fell_back": sum(errors.values()), "errors": dict(errors)}


def halt(a, run_id: str, reason: str) -> dict:
    """A gate stops the run before this trial (called with `budget` held)."""
    if not stop["reason"]:
        stop["reason"] = reason
        progress(a, f"GATE | stop before {run_id}: {reason}")
    return {"run_id": run_id, "skipped": f"stopped: {stop['reason']}"}


def trial(a, log_dir: Path, arm: str, task: str, repeat: int) -> dict:
    run_id = f"{a.label}.{arm}.{task}.r{repeat}"
    out = ROOT / "runs" / a.label / arm / f"{task}-r{repeat}"
    with budget:
        if stop["reason"]:
            return {"run_id": run_id, "skipped": f"stopped: {stop['reason']}"}
        h = health(a.gateway, wait_s=600)
        if h is None:
            return halt(a, run_id, "the gateway did not answer /health for 10 minutes")
        left = h["max_requests_per_day"] - h["requests_today"] - running[0] * h["max_calls_per_run"]
        if left < h["max_calls_per_run"]:
            return halt(a, run_id, f"daily budget: {left} requests left after reserving {running[0]} running "
                                   f"trial(s); a run may need {h['max_calls_per_run']}")
        power = battery()
        if power and power[1] and power[0] <= BATTERY_STOP:
            return halt(a, run_id, f"battery at {power[0]}% and discharging")
        running[0] += 1
    try:
        summary = run_trial(a, log_dir, arm, task, repeat, run_id, out)
    finally:
        with budget:
            running[0] -= 1
    if a.gates and not summary.get("skipped"):
        check(a, summary)
    return summary


def run_trial(a, log_dir: Path, arm: str, task: str, repeat: int, run_id: str, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONPATH": f"{ROOT}:{os.environ.get('PYTHONPATH', '')}", "ECONO_CAFFEINATED": "1"}
    started = time.time()
    if arm in CLM:
        name = f"{arm}-{task}-r{repeat}"
        command = [str(ROOT / "agents" / "clm" / "run.sh"), a.label, task, str(CLM[arm]), str(repeat), arm]
        with (out / "clm-run.out").open("w") as log:
            code = subprocess.run(command, env={**env, "GATEWAY": a.gateway, "MODEL": a.model}, stdout=log,
                                  stderr=subprocess.STDOUT, cwd=ROOT).returncode
    else:
        name = f"{arm.replace('+', '-')}-{task}-r{repeat}"[:60]
        command = [str(ROOT / ".venv" / "bin" / "harbor"), "trial", "start", "-p", str(TBLITE / task), "-e", "docker",
                   "-a", "agents.react.agent:EconoAgent", "-m", f"openai/{a.model}",
                   "--agent-kwarg", f"api_base={a.gateway}/run/{run_id}/v1",
                   "--agent-kwarg", f"econo={ECONO[arm]}", "--agent-kwarg", f"econo_run={run_id}",
                   "--agent-timeout-multiplier", str(a.timeout_multiplier),
                   "--trials-dir", str(out / "harbor"), "--trial-name", name]
        (out / "command.txt").write_text(" ".join(command) + "\n")
        if ECONO[arm] == "jev" and "TYPESAFE_API_KEY" not in env and env_value("TYPESAFE_API_KEY"):
            env["TYPESAFE_API_KEY"] = env_value("TYPESAFE_API_KEY")
        with (out / "harbor.out").open("w") as log:
            code = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, cwd=ROOT).returncode
    return summarize(run_id, arm, task, repeat, out, out / "harbor" / name, log_dir / run_id / "calls.jsonl", code,
                     time.time() - started)


def summarize(run_id: str, arm: str, task: str, repeat: int, out: Path, trial_dir: Path, calls_log: Path, code: int,
              seconds: float) -> dict:
    result = json.loads((trial_dir / "result.json").read_text()) if (trial_dir / "result.json").exists() else {}
    rows = [json.loads(line) for line in calls_log.read_text().splitlines()] if calls_log.exists() else []
    calls = [r for r in rows if r.get("call_no")]
    agent = trial_dir / "agent"
    summary = {"run_id": run_id, "arm": arm, "task": task, "repeat": repeat, "exit": code, "seconds": round(seconds),
               "reward": ((result.get("verifier_result") or {}).get("rewards") or {}).get("reward"),
               "exception": (result.get("exception_info") or {}).get("exception_type"),
               # A request over the model's window, refused by the server: the run counts as a FAIL.
               "overflow": any(r.get("status") == 400 and "ContextWindowExceeded" in json.dumps(r.get("response"))
                               for r in calls),
               "calls": len(calls), "calls_by_kind": dict(Counter(r["kind"] for r in calls)),
               "refused": len(rows) - len(calls), "upstream_retries": sum(r.get("retries") or 0 for r in calls),
               "trial_dir": str(trial_dir.relative_to(ROOT)) if trial_dir.is_relative_to(ROOT) else str(trial_dir),
               "calls_log": str(calls_log.relative_to(ROOT)) if calls_log.is_relative_to(ROOT) else str(calls_log)}
    if arm in CLM:
        usage = json.loads((agent / "usage.json").read_text()) if (agent / "usage.json").exists() else {}
        summary["agent_calls"] = usage.get("n_lm_calls")
        summary["clm"] = {k: usage.get(k) for k in ("context_budget_tokens", "n_ctx_syncs", "n_ctx_syncs_real",
                                                    "n_ctx_grew", "n_ctx_rejected", "n_retry_on_limit",
                                                    "n_turns_rolled_back", "n_nudges", "max_steps")}
    else:
        traj = json.loads((agent / "trajectory.json").read_text()) if (agent / "trajectory.json").exists() else {}
        summary.update(agent_calls=len(traj.get("calls") or []), owner=traj.get("owner"),
                       jev_fallbacks=jev_fallbacks(agent / "session.sqlite3"))
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def measure(summary: dict) -> dict:
    """Rebuild, validate and price one trial's calls (measure/), for the gates and the progress log."""
    with measured:
        if "tokenizer" not in _measure:
            from transformers import AutoTokenizer
            from measure.models import MEASURED, load
            _measure["model"] = load(MEASURED)
            _measure["tokenizer"] = AutoTokenizer.from_pretrained(MEASURED, revision=_measure["model"]["revision"])
        from measure.flops import measure_log
        m = _measure["model"]
        log = ROOT / summary["calls_log"]
        return measure_log(log, _measure["tokenizer"], m["C_token"], m["C_attn"])["totals"] if log.exists() else {}


def check(a, summary: dict) -> None:
    """The per-trial gates; also writes the trial's progress line."""
    t = measure(summary)
    crashed = summary.get("exception") and summary.get("reward") is None and not summary.get("overflow")
    jev = summary.get("jev_fallbacks") or {}
    outcome = ("overflow (FAIL)" if summary.get("overflow") else "pass" if summary.get("reward") == 1.0 else
               "fail" if summary.get("reward") is not None else f"not graded ({summary.get('exception')})")
    with budget:
        progress(a, f"{summary['arm']} | {summary['task']} | r{summary['repeat']} | {outcome} | {summary['calls']} calls | "
                    f"{t.get('F', 0) / 1e15:.3f} PFLOPs | valid {t.get('valid', 0)}/{t.get('valid', 0) + t.get('invalid', 0)} | "
                    f"retries {t.get('retries', 0)} | repeats {t.get('repeats', 0)}"
                    + (f" | Jev {jev.get('asked', 0)} asked, {jev.get('fell_back', 0)} fallbacks {jev.get('errors') or ''}"
                       if summary["arm"] == "econo+jev" else ""))
        crashes[summary["arm"]] = crashes[summary["arm"]] + 1 if crashed else 0
        reasons = []
        if t.get("invalid", 0) >= 3:
            reasons.append(f"{t['invalid']} invalid calls in {summary['run_id']}")
        if crashes[summary["arm"]] >= 2:
            reasons.append(f"{summary['arm']} crashed in 2 trials in a row (last: {summary['exception']})")
        if jev.get("asked") and jev.get("fell_back", 0) > jev["asked"] / 2:
            reasons.append(f"Jev fell back on {jev['fell_back']} of {jev['asked']} questions in {summary['run_id']}")
        if reasons and not stop["reason"]:
            stop["reason"] = "; ".join(reasons)
            progress(a, f"GATE | stop after {summary['run_id']}: {stop['reason']}")


def jobs_for(arms: list[str], tasks: list[str], first: int, repeats: int) -> list[tuple[str, str, int]]:
    """Interleaved: for each repeat, for each task, every arm, so arms run close in time."""
    return [(arm, task, r) for r in range(first, first + repeats) for task in tasks for arm in arms]


def keep_awake() -> None:
    if sys.platform != "darwin":
        return
    subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
    power = battery()
    if power and power[1]:
        print(f"WARNING: on battery power ({power[0]}%); plug in for long runs. The run stops at {BATTERY_STOP}%.")
    lid = subprocess.run(["ioreg", "-r", "-k", "AppleClamshellState", "-d", "4"], capture_output=True, text=True).stdout
    state = "closed" if '"AppleClamshellState" = Yes' in lid else "open" if "AppleClamshellState" in lid else "unknown"
    print(f"reminder: keep the lid open; a closed lid sleeps the Mac even with caffeinate (lid now: {state})")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--label", required=True)
    p.add_argument("--arm", required=True, help=f"one arm or a comma-separated list of: {', '.join(ARMS)}")
    p.add_argument("--tasks", required=True, help="comma-separated task names, or 'frozen' for the 10")
    p.add_argument("--model", required=True, help="the provider's model id, e.g. qwen3.8:27b")
    p.add_argument("--gateway", default="http://127.0.0.1:8787")
    p.add_argument("--repeats", type=int, default=1)
    p.add_argument("--repeat-from", type=int, default=1, help="number the repeats from here (r2, r3, ...)")
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--timeout-multiplier", type=float, default=4)
    p.add_argument("--gates", action="store_true", help="measure each trial and stop on the quality gates")
    p.add_argument("--progress", help="append one line per finished trial and every gate decision here")
    a = p.parse_args()
    arms = a.arm.split(",")
    unknown = [arm for arm in arms if arm not in ARMS]
    if unknown:
        raise SystemExit(f"unknown arm(s) {unknown}; choose from {ARMS}")
    keep_awake()
    h = health(a.gateway)
    if h is None:
        raise SystemExit(f"the gateway is not running at {a.gateway}")
    log_dir = Path(h["log_dir"])
    tasks = FROZEN if a.tasks == "frozen" else a.tasks.split(",")
    jobs = jobs_for(arms, tasks, a.repeat_from, a.repeats)
    with budget:
        progress(a, f"START | {a.label}: {len(jobs)} trials ({', '.join(arms)}; {len(tasks)} tasks; repeats "
                    f"{a.repeat_from}-{a.repeat_from + a.repeats - 1}), {a.workers} workers; {h['requests_today']} of "
                    f"{h['max_requests_per_day']} requests used today")
    queue, results = list(jobs), []

    def worker():
        while True:
            with budget:
                if not queue or stop["reason"]:
                    return
                job = queue.pop(0)
            summary = trial(a, log_dir, *job)
            results.append(summary)
            if not a.gates and not summary.get("skipped"):
                print(json.dumps(summary), flush=True)
    threads = [threading.Thread(target=worker) for _ in range(a.workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    done = [r for r in results if not r.get("skipped")]
    with budget:
        progress(a, f"END | {a.label}: {len(done)} of {len(jobs)} trials finished"
                    + (f"; stopped: {stop['reason']} ({len(queue) + len(results) - len(done)} not run)" if stop["reason"] else ""))


if __name__ == "__main__":
    main()
