"""Run arm x task x rep through Harbor, the same way CLM's run_harbor.sh does.

  python -m econoclm.bench.tblite.run --arms raw,econo --tasks tasks.txt --reps 1 \
      --workers 4 [--limit N] [--dry-run]

For each (arm, task, rep):
  run_id   = f"{arm}-{task}-r{rep}"
  api_base = http://127.0.0.1:8787/run/{run_id}/v1   (our measure-only gateway)
and the command is

  harbor trial start -p <task_dir> -e docker -a <agent> -m <model>
      --agent-kwarg api_base=<api_base> --agent-kwarg k=v ... (from the arm's config)
      --agent-timeout-multiplier 4 --trials-dir runs/<date>/harbor --trial-name <run_id>

with PYTHONPATH = <clm repo>/clm : <our repo> and OPENAI_API_KEY=placeholder. The
Vertex key is never in the agent's environment (only the gateway reads it).

Fairness: jobs are queued task by task with the arms alternating (raw, econo, raw,
...), so when both arms run in one invocation they meet the same quota conditions.
Before each launch the gateway's total spend is checked; past MAX_SPEND_USD no new
trial starts.

Outputs per run: runs/<date>/<run_id>/command.txt (exact command line),
harbor.log (Harbor's output), econo.sqlite (EconoCLM only).
"""

import argparse
import datetime as dt
import os
import shlex
import subprocess
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

from ...core.gateway import prior_spend
from ...core.gateway_ledger import Ledger
from ...core.tokenizer import use_bundled_tokenizer

ECONOCLM = Path(__file__).resolve().parents[2]          # .../econoclm
REPO = ECONOCLM.parent                                  # our repo root
ARMS = {"raw": ECONOCLM / "arms/raw_clm/config.yaml",
        "econo": ECONOCLM / "arms/econo_clm/config.yaml"}
PATH_KWARGS = {"skill_dirs"}                            # relative to econoclm/ in configs
SECRET_ENV = {"AGENT_PLATFORM_API_KEY", "ECONOCONTEXT_BASE_URL", "ECONOCONTEXT_ANTHROPIC_KEY"}
TIMEOUT_MULTIPLIER = 4                                  # CLM's TBLite setup: 4x task time


def fmt(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def load_arm(arm: str) -> dict:
    return yaml.safe_load(ARMS[arm].read_text())


def build_command(arm: str, task: str, rep: int, *, tblite: Path, out_dir: Path,
                  port: int, harbor: str = "harbor") -> tuple[str, list[str]]:
    """(run_id, argv) for one trial."""
    cfg = load_arm(arm)
    run_id = f"{arm}-{task}-r{rep}"
    api_base = f"http://127.0.0.1:{port}/run/{run_id}/v1"
    kwargs = [f"api_base={api_base}"]
    for k, v in (cfg.get("agent_kwargs") or {}).items():
        if k in PATH_KWARGS:
            v = ",".join(str((ECONOCLM / p).resolve()) for p in str(v).split(","))
        kwargs.append(f"{k}={fmt(v)}")
    if arm == "econo":
        kwargs.append(f"econo_run_dir={out_dir / run_id}")
    argv = [harbor, "trial", "start", "-p", str(tblite / task), "-e", "docker",
            "-a", cfg["agent"], "-m", cfg["model"]]
    for kv in kwargs:
        argv += ["--agent-kwarg", kv]
    argv += ["--agent-timeout-multiplier", str(TIMEOUT_MULTIPLIER),
             "--trials-dir", str(out_dir / "harbor"), "--trial-name", run_id]
    return run_id, argv


def agent_env(clm_repo: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in SECRET_ENV}
    use_bundled_tokenizer(env)  # same tokenizer file for both arms, on every machine
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(clm_repo / "clm"), str(REPO), os.environ.get("PYTHONPATH")) if p)
    env["OPENAI_API_KEY"] = "placeholder"
    return env


def jobs(arms: list[str], tasks: list[str], reps: int) -> list[tuple[str, str, int]]:
    """Task by task, arms alternating, so both arms see the same conditions."""
    return [(arm, task, rep) for rep in range(1, reps + 1) for task in tasks for arm in arms]


def spend(out_dir: Path) -> float:
    """Spend so far over the whole experiment: this folder's ledger plus the other
    runs/*/gateway.sqlite (the same total the gateway's cap uses)."""
    db = out_dir / "gateway.sqlite"
    total = prior_spend(db)
    if db.exists():
        ledger = Ledger(db)
        try:
            total += ledger.total_spend()
        finally:
            ledger.close()
    return total


def gateway_up(port: int) -> bool:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3)
    except urllib.error.HTTPError:
        return True   # it answered (404 for GET is expected)
    except Exception:
        return False
    return True


def run_one(argv: list[str], run_dir: Path, env: dict, max_spend: float, out_dir: Path) -> int:
    spent = spend(out_dir)
    if spent > max_spend:
        print(f"SKIP {run_dir.name}: spend ${spent:.2f} > cap ${max_spend:.2f}", flush=True)
        return -1
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "command.txt").write_text(shlex.join(argv) + "\n")
    print(f"START {run_dir.name}", flush=True)
    with open(run_dir / "harbor.log", "w") as logf:
        code = subprocess.call(argv, env=env, stdout=logf, stderr=subprocess.STDOUT)
    print(f"END   {run_dir.name} exit={code}", flush=True)
    return code


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--arms", default="raw,econo")
    ap.add_argument("--tasks", type=Path, default=Path(__file__).parent / "tasks.txt")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=None, help="only the first N tasks")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--date", default=dt.date.today().isoformat(),
                    help="name of the runs/ folder for this phase, e.g. 2026-10-04-main")
    ap.add_argument("--runs", type=Path, default=ECONOCLM / "runs")
    ap.add_argument("--tblite", type=Path, default=REPO.parent / "OpenThoughts-TBLite")
    ap.add_argument("--clm-repo", type=Path, default=REPO.parent / "context-language-models")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--harbor", default=str(Path(sys.executable).with_name("harbor")))
    args = ap.parse_args()

    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    tasks = [t.strip() for t in args.tasks.read_text().splitlines() if t.strip()]
    tasks = tasks[:args.limit] if args.limit else tasks
    out_dir = (args.runs / args.date).resolve()
    max_spend = float(os.environ.get("MAX_SPEND_USD", "40"))

    plan = []
    for arm, task, rep in jobs(arms, tasks, args.reps):
        run_id, argv = build_command(arm, task, rep, tblite=args.tblite.resolve(),
                                     out_dir=out_dir, port=args.port, harbor=args.harbor)
        plan.append((run_id, argv))
    if args.dry_run:
        for _, argv in plan:
            print(shlex.join(argv))
        return

    if not gateway_up(args.port):
        raise SystemExit(f"no gateway on 127.0.0.1:{args.port}; start econoclm.core.gateway first")
    if not (out_dir / "gateway.sqlite").exists():
        raise SystemExit(f"{out_dir / 'gateway.sqlite'} does not exist: start the gateway with "
                         f"--ledger {out_dir / 'gateway.sqlite'} (one ledger per runs folder)")
    env = agent_env(args.clm_repo.resolve())
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        codes = list(pool.map(lambda p: run_one(p[1], out_dir / p[0], env, max_spend, out_dir),
                              plan))
    print(f"done: {len(codes)} trials, exits {codes}, spend ${spend(out_dir):.4f}")


if __name__ == "__main__":
    main()
