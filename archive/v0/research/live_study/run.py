"""Run the live study: tasks x models x policies, each a real run at real cost.

    python research/live_study/run.py --study pilot --cap 40 \
        --models gemini-3.5-flash gpt-oss-120b --policies P0 --tasks PyCQA__pyflakes-325

Spending is guarded twice. Inside a run, the harness refuses any call whose
worst-case price would take the run past --per-run. Across runs, a run starts
only if the study's remaining budget covers a whole --per-run, so the study can
never pass --cap. Every run, including failed ones, is appended to the ledger
with its cost before the next one starts.
"""

import argparse
import asyncio
import json
import time
from datetime import date
from pathlib import Path

from models import CONTEXT_TOKENS, MODELS, config
from policies import POLICIES

from agents import build
from econocontext.config import load_env_file
from econocontext.contracts import Limits, RunRequest, Task
from econocontext.runtime.manager import Manager

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "src" / "agents" / "swe" / "fixtures"


def spent(ledger):
    if not ledger.exists():
        return 0.0
    return sum(json.loads(line)["cost"] or 0 for line in ledger.read_text().splitlines())


THROTTLED = "429 Too Many Requests"
ATTEMPTS_PER_CELL = 2


def done(ledger):
    """Cells that need no further run: finished, or already given every attempt.

    A run killed by provider throttling says nothing about the policy, so its
    cell is run again, once. Its spend stays in the ledger and counts to the cap.
    """
    if not ledger.exists():
        return set()
    tries = {}
    finished = set()
    for r in map(json.loads, ledger.read_text().splitlines()):
        cell = (r["task"], r["model"], r["policy"])
        tries[cell] = tries.get(cell, 0) + 1
        if THROTTLED not in (r.get("reason") or "") or tries[cell] >= ATTEMPTS_PER_CELL:
            finished.add(cell)
    return finished


def headroom(ledger, cap, per_run):
    """The spending limit for the next run, or None if the study must stop."""
    remaining = cap - spent(ledger)
    return per_run if remaining >= per_run else None


def limits(args, max_cost, overrides):
    return Limits(
        **overrides,
        # The agent gets max_turns turns of its own under every policy. Delegated
        # calls are billed but do not use them up; 4x bounds the total regardless.
        max_root_turns=args.max_turns,
        max_attempts=min(1000, 4 * args.max_turns),
        output_tokens=args.output_tokens,
        context_tokens=CONTEXT_TOKENS,
        max_cost=max_cost,
        tool_timeout=180,
        deadline=args.deadline,
        # Shared-capacity throttling (429) is transient and unbilled; backoff
        # reaches 60s, so eight retries ride out about three minutes of it.
        retries=8,
    )


async def run_one(args, out, task, model, policy, max_cost):
    cell = out / task / model / policy
    cell.mkdir(parents=True, exist_ok=True)
    method, overrides, limit_overrides = POLICIES[policy]
    if args.smoke_trigger:
        overrides = dict(overrides, context_trigger=args.smoke_trigger)
    # One store per study. A harness marks any run still "running" in its store as
    # interrupted when it starts, so two studies sharing a store stop each other.
    store = ROOT / "data" / "live-study" / args.study
    cfg = config(model, store, ROOT / "logs").model_copy(update=overrides)
    manager = await Manager(cfg, adapter=build).start()
    started = time.time()
    try:
        request = RunRequest(
            task=Task(adapter="swe", fixture=task),
            method=method,
            limits=limits(args, max_cost, limit_overrides),
            idempotency_key=f"{args.study}:{task}:{model}:{policy}:{int(started)}",
        )
        run = await manager.submit(request)
        final = await manager.wait(run["id"])
        metrics = await manager.telemetry.metrics(run["id"])
        events = await manager.memory.all_events(run["id"])
    finally:
        await manager.close()
    (cell / "trace.json").write_text(
        json.dumps(dict(run=final, metrics=metrics, trace=events), indent=1, default=str)
    )
    details = final.get("verification_details") or {}
    return dict(
        at=int(started),
        study=args.study,
        task=task,
        model=model,
        policy=policy,
        run_id=final["id"],
        status=final["status"],
        reason=final.get("reason"),
        resolved=final.get("verification") == "verified",
        fail_to_pass_failing=len((details.get("fail_to_pass") or {}).get("failing", [])),
        pass_to_pass_failing=len((details.get("pass_to_pass") or {}).get("failing", [])),
        cost=metrics["known_cost"],
        cost_complete=metrics.get("cost_complete"),
        tokens=metrics.get("tokens"),
        model_calls=metrics.get("model_attempts"),
        wall_seconds=metrics.get("wall_seconds"),
        pricing=MODELS[model]["pricing"].revision,
    )


async def main(args):
    out = ROOT / "docs" / "runs" / f"{args.date}-live-study" / args.study
    out.mkdir(parents=True, exist_ok=True)
    ledger = out / "ledger.jsonl"
    tasks = args.tasks or sorted(p.name for p in FIXTURES.iterdir() if p.is_dir())
    for task in tasks:
        for model in args.models:
            for policy in args.policies:
                while (task, model, policy) not in done(ledger):
                    if not await cell(args, out, ledger, task, model, policy):
                        return


async def cell(args, out, ledger, task, model, policy):
    """Run one cell once and record it. False when the budget stops the study."""
    max_cost = headroom(ledger, args.cap, args.per_run)
    if max_cost is None:
        print(
            f"STOP: ${spent(ledger):.2f} of ${args.cap:.2f} spent; "
            f"not enough left for a ${args.per_run:.2f} run"
        )
        return False
    print(f"-> {task} / {model} / {policy}  (run cap ${max_cost:.2f})", flush=True)
    record = await run_one(args, out, task, model, policy, max_cost)
    with ledger.open("a") as f:
        f.write(json.dumps(record) + "\n")
    print(
        f"   {record['status']}  resolved={record['resolved']}  "
        f"${record['cost']:.4f}  {record['model_calls']} calls  "
        f"{record['wall_seconds']:.0f}s   study total ${spent(ledger):.4f}",
        flush=True,
    )
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--study", required=True)
    parser.add_argument(
        "--cap", type=float, required=True, help="Hard cap for the whole study, USD"
    )
    parser.add_argument("--per-run", type=float, default=5.0, help="Hard cap for one run, USD")
    parser.add_argument("--models", nargs="+", choices=sorted(MODELS), required=True)
    parser.add_argument("--policies", nargs="+", choices=sorted(POLICIES), default=["P0"])
    parser.add_argument("--tasks", nargs="*")
    parser.add_argument("--max-turns", type=int, default=100)
    parser.add_argument("--output-tokens", type=int, default=16384)
    parser.add_argument("--deadline", type=float, default=5400)
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument(
        "--smoke-trigger",
        type=int,
        help="Lower the context threshold so short smoke runs exercise every policy",
    )
    arguments = parser.parse_args()
    if arguments.smoke_trigger and not arguments.study.startswith("smoke"):
        parser.error("--smoke-trigger changes the experiment; only smoke studies may use it")
    load_env_file(ROOT / ".env")
    asyncio.run(main(arguments))
