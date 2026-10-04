"""Run the CLM / EconoCLM-Tools / EconoCLM-View arms on Qwen3.6-27B (local vLLM). One command.

  bash econoclm/bench/tblite/run_qwen.sh --arms clm --reps 3          # 30 trials
  bash econoclm/bench/tblite/run_qwen.sh --arms clm,econo_tools --reps 3 --dry-run

Steps:
  a. check the vLLM server (backends/qwen/serve.sh): it answers, serves qwen36-27b, and
     reports cached tokens (prompt_tokens_details.cached_tokens); stop if not;
  b. one check trial per arm on the first task; it must produce a reward and FLOPs
     (trajectory.ctx.json); stop if not;
  c. the rest: tasks x arms x reps (checks included), interleaved by task (run.jobs),
     --workers at a time (default 4), resumable: finished trials are skipped, unfinished
     ones are removed and rerun;
  d. print the check trials' wall time and a projected total;
  f. write runs-qwen/<date>/REPORT_QWEN.md (analysis/flops_report.py).

Arms: clm (default), econo_tools, econo_view. EconoCLM-View is LOCKED until its fixed
Gemini check is approved: it runs only with --allow-unvalidated.
No gateway: agents call vLLM directly (api_base http://127.0.0.1:<port>/v1, key EMPTY).
Harbor runs every trial with --agent-timeout-multiplier 4. run.py (a frozen shared part)
is imported, not changed.
"""

import argparse
import datetime as dt
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

from . import run

ARMS = {  # CLI name -> (run-id prefix, config); run ids have no "-" in the arm part
    "clm": ("clm", run.ECONOCLM / "arms/clm_qwen/config.yaml"),
    "econo_tools": ("econotools", run.ECONOCLM / "arms/econo_tools_qwen/config.yaml"),
    "econo_view": ("econoview", run.ECONOCLM / "arms/econo_view_qwen/config.yaml"),
}
LOCKED = {"econo_view": "EconoCLM-View is awaiting its fixed Gemini check (REPORT.md deviation 9); "
                        "run it only when told to, with --allow-unvalidated."}
SERVED = "qwen36-27b"
SERVER_JSON = Path.home() / ".econoclm" / "qwen_server.json"


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def http(url: str, body: dict | None = None, timeout: int = 120) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data, {"Content-Type": "application/json",
                                             "Authorization": "Bearer EMPTY"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    try:
        return json.loads(raw)
    except ValueError:
        return {"text": raw.decode(errors="replace")}


def check_server(port: int) -> dict:
    """Server answers, serves qwen36-27b, and reports cached tokens on a repeated prompt."""
    base = f"http://127.0.0.1:{port}"
    try:
        models = [m["id"] for m in http(f"{base}/v1/models", timeout=10).get("data", [])]
    except Exception as exc:
        raise SystemExit(f"STOP: no vLLM server on {base} ({exc}). Start backends/qwen/serve.sh in tmux "
                         "and wait until it logs that it is serving.")
    if SERVED not in models:
        raise SystemExit(f"STOP: the server serves {models}, not {SERVED}. Use backends/qwen/serve.sh.")
    try:
        version = http(f"{base}/version", timeout=10).get("version")
    except Exception:
        version = None
    text = " ".join(f"Line {i}: the quick brown fox jumps over the lazy dog." for i in range(120))
    body = {"model": SERVED, "max_tokens": 8, "messages": [{"role": "user", "content": text + "\nReply OK."}],
            "chat_template_kwargs": {"enable_thinking": False}}
    cached = None
    for _ in range(2):
        usage = http(f"{base}/v1/chat/completions", body).get("usage") or {}
        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
    if not cached:
        raise SystemExit("STOP: the server does not report cached tokens (prompt_tokens_details.cached_tokens "
                         f"= {cached!r} on a repeated prompt). Start vLLM with --enable-prefix-caching "
                         "--enable-prompt-tokens-details (backends/qwen/serve.sh does).")
    log(f"server OK: {SERVED}, vLLM {version}, cached tokens reported ({cached} on a repeated prompt)")
    return {"vllm_version_live": version, "cached_probe": cached}


def build(arm: str, task: str, rep: int, *, tblite: Path, trials: Path, port: int,
          harbor: str) -> tuple[str, list[str]]:
    prefix, cfg_path = ARMS[arm]
    cfg = yaml.safe_load(cfg_path.read_text())
    run_id = f"{prefix}-{task}-r{rep}"
    kwargs = [f"api_base=http://127.0.0.1:{port}/v1"]
    for k, v in (cfg.get("agent_kwargs") or {}).items():
        if k in run.PATH_KWARGS:
            v = ",".join(str((run.ECONOCLM / p).resolve()) for p in str(v).split(","))
        kwargs.append(f"{k}={run.fmt(v)}")
    if arm != "clm":
        kwargs.append(f"econo_run_dir={trials / run_id}")
    argv = [harbor, "trial", "start", "-p", str(tblite / task), "-e", "docker",
            "-a", cfg["agent"], "-m", cfg["model"]]
    for kv in kwargs:
        argv += ["--agent-kwarg", kv]
    argv += ["--agent-timeout-multiplier", str(run.TIMEOUT_MULTIPLIER),
             "--trials-dir", str(trials / "harbor"), "--trial-name", run_id]
    return run_id, argv


def finished(trials: Path, run_id: str) -> bool:
    return (trials / "harbor" / run_id / "result.json").exists()


def run_trial(run_id: str, argv: list[str], trials: Path, env: dict) -> float:
    for stale in (trials / "harbor" / run_id, trials / run_id):
        if stale.exists() and not finished(trials, run_id):
            shutil.rmtree(stale)                         # an unfinished trial is rerun
    d = trials / run_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "command.txt").write_text(shlex.join(argv) + "\n")
    t0 = time.monotonic()
    log(f"START {run_id}")
    with open(d / "harbor.log", "w") as out:
        rc = subprocess.run(argv, stdout=out, stderr=subprocess.STDOUT, env=env).returncode
    wall = time.monotonic() - t0
    log(f"END   {run_id} exit={rc} ({wall / 60:.1f} min)")
    return wall


def check_trial_ok(trials: Path, run_id: str) -> str | None:
    """None if the trial has a reward and FLOPs, else what is missing."""
    res = trials / "harbor" / run_id / "result.json"
    if not res.exists():
        return "no result.json"
    r = json.loads(res.read_text())
    reward = ((r.get("verifier_result") or {}).get("rewards") or {}).get("reward")
    if reward is None:
        exc = (r.get("exception_info") or {}).get("exception_message", "")
        return f"no reward ({str(exc)[:200]})"
    ctx = trials / "harbor" / run_id / "agent" / "trajectory.ctx.json"
    if not ctx.exists():
        return "no trajectory.ctx.json"
    kv = ((json.loads(ctx.read_text()).get("final_metrics") or {}).get("extra") or {}).get("kv_cache_flops") or {}
    if not kv.get("cache_aware_flops"):
        return f"no FLOPs (kv_cache_flops source {kv.get('prefill_source')})"
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--arms", default="clm", help="comma list of clm, econo_tools, econo_view (default clm)")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--tasks", type=Path, default=Path(__file__).parent / "tasks.txt")
    ap.add_argument("--date", default=dt.date.today().isoformat())
    ap.add_argument("--runs", type=Path, default=run.REPO / "runs-qwen")
    ap.add_argument("--tblite", type=Path, default=run.REPO.parent / "OpenThoughts-TBLite")
    ap.add_argument("--clm-repo", type=Path, default=run.REPO.parent / "context-language-models")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--harbor", default=str(Path(sys.executable).with_name("harbor")))
    ap.add_argument("--allow-unvalidated", action="store_true",
                    help="allow EconoCLM-View before its fixed Gemini check is approved")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-checks", action="store_true", help="resume without rerunning a/b")
    args = ap.parse_args()

    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    for a in arms:
        if a not in ARMS:
            raise SystemExit(f"unknown arm {a!r}; choose from {', '.join(ARMS)}")
        if a in LOCKED and not args.allow_unvalidated:
            raise SystemExit(f"STOP: {LOCKED[a]}")
    tasks = [t.strip() for t in args.tasks.read_text().splitlines() if t.strip()]
    out = (args.runs / args.date).resolve()
    trials = out / "trials"
    tblite = args.tblite.resolve()
    plan = [(a, t, r) for a, t, r in run.jobs(arms, tasks, args.reps)]
    cmds = {(a, t, r): build(a, t, r, tblite=tblite, trials=trials, port=args.port, harbor=args.harbor)
            for a, t, r in plan}
    if args.dry_run:
        for key in plan:
            print(shlex.join(cmds[key][1]))
        print(f"# {len(plan)} trials: arms {arms}, {len(tasks)} tasks, {args.reps} reps", file=sys.stderr)
        return

    trials.mkdir(parents=True, exist_ok=True)
    env = run.agent_env(args.clm_repo.resolve())
    env["OPENAI_API_KEY"] = "EMPTY"
    settings = {"arms": arms, "reps": args.reps, "workers": args.workers, "tasks": tasks,
                "configs": {a: yaml.safe_load(ARMS[a][1].read_text()) for a in arms},
                "timeout_multiplier": run.TIMEOUT_MULTIPLIER, "port": args.port,
                "server_settings": json.loads(SERVER_JSON.read_text()) if SERVER_JSON.exists() else None}

    # a. the server
    if not args.skip_checks:
        settings.update(check_server(args.port))
    (out / "settings.json").write_text(json.dumps(settings, indent=2) + "\n")

    # b. one check trial per arm on the first task
    checks = [(a, tasks[0], 1) for a in arms]
    walls: dict[str, float] = {}
    if not args.skip_checks:
        todo = [k for k in checks if not finished(trials, cmds[k][0])]
        with ThreadPoolExecutor(max_workers=max(1, min(args.workers, len(todo) or 1))) as pool:
            for k, wall in zip(todo, pool.map(lambda k: run_trial(*cmds[k], trials, env), todo)):
                walls[cmds[k][0]] = wall
        bad = {cmds[k][0]: check_trial_ok(trials, cmds[k][0]) for k in checks}
        bad = {rid: why for rid, why in bad.items() if why}
        if bad:
            for rid, why in bad.items():
                log(f"CHECK FAILED {rid}: {why} (see {trials / rid / 'harbor.log'})")
            raise SystemExit("STOP: a check trial did not produce a reward and FLOPs.")
        log("check trials OK: " + ", ".join(cmds[k][0] for k in checks))

    # d. projection
    rest = [k for k in plan if not finished(trials, cmds[k][0])]
    if walls:
        mean = sum(walls.values()) / len(walls)
        total = mean * len(rest) / max(1, args.workers)
        log(f"check trials: mean wall {mean / 60:.1f} min; {len(rest)} trials left at {args.workers} "
            f"at a time -> about {total / 3600:.1f} h (projection from {len(walls)} check trial(s))")

    # c. the rest, interleaved by task, resumable
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(lambda k: run_trial(*cmds[k], trials, env), rest))
    log(f"all trials done in {(time.monotonic() - t0) / 3600:.2f} h")

    # f. the report
    subprocess.run([sys.executable, "-m", "econoclm.analysis.flops_report", str(out)], env=env, check=False)
    log(f"report: {out / 'REPORT_QWEN.md'}")


if __name__ == "__main__":
    main()
