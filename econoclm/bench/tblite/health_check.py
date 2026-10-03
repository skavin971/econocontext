"""Health check: does each chosen task pass with its own reference solution?

  python -m econoclm.bench.tblite.health_check [--dry-run] [--write] [--workers 4]

No model is called. Each task runs once under Harbor's built-in `oracle` agent,
which executes the task's solution/ script, and the task's tests grade it:

  harbor trial start -p <task> -e docker -a oracle --agent-timeout-multiplier 4 \
      --trials-dir runs/<date>/health --trial-name oracle-<task>

A task passes when result.json has reward 1. A task that fails (lower reward, an
exception, no result) is swapped for the next untried task in the seeded order
(select_tasks.seeded_order), which is then checked too, until 10 tasks pass or the
list runs out. A broken task would otherwise count as a failure for both arms and
only add noise.

The summary is printed and saved to runs/<date>/health.md. With --write, tasks.txt
is replaced by the 10 healthy tasks; without it the proposed list is only printed.
"""

import argparse
import datetime as dt
import json
import shlex
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from .run import ECONOCLM, REPO, TIMEOUT_MULTIPLIER, agent_env
from .select_tasks import N_TASKS, all_tasks, seeded_order

HERE = Path(__file__).parent


@dataclass
class Check:
    task: str
    reward: float | None = None
    error: str | None = None
    seconds: float | None = None

    @property
    def passed(self) -> bool:
        return self.reward == 1


def oracle_command(task: str, *, tblite: Path, out_dir: Path, harbor: str) -> list[str]:
    return [harbor, "trial", "start", "-p", str(tblite / task), "-e", "docker", "-a", "oracle",
            "--agent-timeout-multiplier", str(TIMEOUT_MULTIPLIER),
            "--trials-dir", str(out_dir / "health"), "--trial-name", f"oracle-{task}"]


def read_result(trial_dir: Path) -> tuple[float | None, str | None]:
    """(reward, error) from Harbor's result.json."""
    path = trial_dir / "result.json"
    if not path.exists():
        return None, "no result.json"
    data = json.loads(path.read_text())
    exc = data.get("exception_info")
    rewards = (data.get("verifier_result") or {}).get("rewards") or {}
    reward = rewards.get("reward")
    if exc:
        return reward, f"{exc.get('exception_type')}: {(exc.get('exception_message') or '')[:120]}"
    return reward, None if reward is not None else "no reward"


def run_check(task: str, *, tblite: Path, out_dir: Path, harbor: str, env: dict) -> Check:
    argv = oracle_command(task, tblite=tblite, out_dir=out_dir, harbor=harbor)
    log_dir = out_dir / "health_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"{task}.command.txt").write_text(shlex.join(argv) + "\n")
    t0 = time.monotonic()
    with open(log_dir / f"{task}.log", "w") as logf:
        subprocess.call(argv, env=env, stdout=logf, stderr=subprocess.STDOUT)
    reward, error = read_result(out_dir / "health" / f"oracle-{task}")
    return Check(task, reward, error, round(time.monotonic() - t0, 1))


def heal(chosen: list[str], order: list[str], check, workers: int = 4) -> tuple[list[str], list[Check], list[tuple[str, str]]]:
    """Check `chosen`; swap each failure for the next untried task in `order`.
    Returns (healthy list in the original slots, every check made, swaps old -> new).
    `check(task) -> Check` is injected so the logic is testable without Docker."""
    spare = [t for t in order if t not in chosen]
    slots = list(chosen)
    checks: list[Check] = []
    swaps: list[tuple[str, str]] = []
    pending = list(range(len(slots)))       # slot indexes still to (re)check
    with ThreadPoolExecutor(max_workers=workers) as pool:
        while pending:
            results = list(pool.map(lambda i: check(slots[i]), pending))
            checks += results
            failed = [i for i, r in zip(pending, results) if not r.passed]
            pending = []
            for i in failed:
                if not spare:
                    slots[i] = None
                    continue
                new = spare.pop(0)
                swaps.append((slots[i], new))
                slots[i] = new
                pending.append(i)
    return [t for t in slots if t], checks, swaps


def summary(healthy: list[str], checks: list[Check], swaps: list[tuple[str, str]]) -> str:
    lines = ["# Task health check (oracle = reference solution)", "",
             "| Task | Passed | Reward | Time (s) | Error |", "|---|---|---|---|---|"]
    for c in checks:
        lines.append(f"| {c.task} | {'yes' if c.passed else 'NO'} | {c.reward} | {c.seconds} | "
                     f"{(c.error or '').replace('|', '/')} |")
    lines += ["", f"Swaps: {len(swaps)}"] + [f"- {old} → {new}" for old, new in swaps]
    lines += ["", f"Healthy tasks ({len(healthy)}):"] + [f"- {t}" for t in healthy]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tasks", type=Path, default=HERE / "tasks.txt")
    ap.add_argument("--tblite", type=Path, default=REPO.parent / "OpenThoughts-TBLite")
    ap.add_argument("--clm-repo", type=Path, default=REPO.parent / "context-language-models")
    ap.add_argument("--runs", type=Path, default=ECONOCLM / "runs")
    ap.add_argument("--date", default=dt.date.today().isoformat())
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--harbor", default=str(Path(sys.executable).with_name("harbor")))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--write", action="store_true", help="replace tasks.txt with the healthy list")
    args = ap.parse_args()

    tblite = args.tblite.resolve()
    out_dir = (args.runs / args.date).resolve()
    chosen = [t.strip() for t in args.tasks.read_text().splitlines() if t.strip()]
    if args.dry_run:
        for t in chosen:
            print(shlex.join(oracle_command(t, tblite=tblite, out_dir=out_dir, harbor=args.harbor)))
        return

    env = agent_env(args.clm_repo.resolve())
    order = seeded_order(all_tasks(tblite))
    healthy, checks, swaps = heal(
        chosen, order,
        lambda t: run_check(t, tblite=tblite, out_dir=out_dir, harbor=args.harbor, env=env),
        workers=args.workers)
    text = summary(healthy, checks, swaps)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "health.md").write_text(text)
    print(text)
    if len(healthy) < N_TASKS:
        print(f"WARNING: only {len(healthy)} healthy tasks")
    if args.write and healthy != chosen:
        args.tasks.write_text("\n".join(healthy) + "\n")
        print(f"wrote {args.tasks}")
    elif swaps:
        print("tasks.txt unchanged (pass --write to apply the swaps)")


if __name__ == "__main__":
    main()
