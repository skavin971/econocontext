"""Run SWE-bench instances with stock Deep Agents, with or without EconoContext.

This is the composition root: the only file under hosts/ that knows EconoContext
exists. The two arms differ ONLY in whether the decision middleware is installed;
measurement (the usage callback) is installed in both, so both are measured the
same way.

    python -m hosts.swebench_deepagents.run --label check1 --arm baseline --set dev
    python -m hosts.swebench_deepagents.run --label check1 --arm econo --set dev --mode observe
    python -m hosts.swebench_deepagents.run --label check1 --arm econo --set dev --mode observe --jev

--jev (econo arm only): the planner asks Jev (econocontext/planner/jev_planner.py)
how likely each tool result is to be needed again, instead of the fixed guess.
Runs with --jev are saved as "econo+jev" so they never mix with runs without it.

Budgets: a run stops when its instance budget or the label's total budget would be
exceeded (the callback refuses the next model call); a stopped run counts as failed,
and its spend counts. NOTE: dollar budgets need econocontext/pricing/ledger.py to be
built; until then only the step limit (limits.per_instance_step_limit) caps a run.
"""

import argparse
import json
import time
from pathlib import Path

from adapters.deepagents import (BudgetExceeded, DeepAgentsHost, install_decisions,
                                 install_measurement)
from econocontext import config as config_module
from econocontext.engine import EconoContext
from econocontext.store.db import AgentDB

from . import agent, tasks
from .evaluate import evaluate
from .sandbox import SweBenchSandbox

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"


def spent_on_label(db_path: str, label: str) -> float:
    db = AgentDB(db_path)
    rows = db.rows("SELECT COALESCE(SUM(cost_usd), 0) AS usd FROM outcomes WHERE run_id LIKE ?",
                   (f"{label}:%",))
    return float(rows[0]["usd"])


def track(arm: str, jev: bool) -> str:
    """'baseline', 'econo' or 'econo+jev': used in run ids and file names."""
    return f"{arm}+jev" if jev and arm == "econo" else arm


def run_instance(inst: tasks.Instance, arm: str, mode: str, label: str, cfg: dict,
                 db_path: str, jev: bool = False) -> tuple[str, str]:
    limits = cfg["limits"]
    run_id = f"{label}:{track(arm, jev)}:{inst.instance_id}:{int(time.time())}"
    with SweBenchSandbox(inst.image) as sandbox:
        host = DeepAgentsHost(sandbox)
        engine = EconoContext(str(CONFIG_DIR), host, run_id, host_name="swebench_deepagents",
                              arm=arm, instance_id=inst.instance_id, db_path=db_path,
                              mode=mode if arm == "econo" else "measure",
                              jev=jev and arm == "econo")
        callbacks = install_measurement(engine, limits["per_instance_budget_usd"],
                                        spent_on_label(db_path, label), limits["live_run_budget_usd"])
        middleware, sub_middleware = [], []
        if arm == "econo":
            make = install_decisions(engine, host)
            middleware, sub_middleware = [make()], [make()]
        llm = agent.model(cfg["model"]["name"], cfg["model"]["temperature"])
        graph = agent.build(llm, sandbox, limits["per_instance_step_limit"], middleware,
                            sub_middleware)
        status = "completed"
        try:
            graph.invoke({"messages": [("user", inst.problem_statement)]},
                         {"callbacks": callbacks, "recursion_limit": 10_000})
        except BudgetExceeded as exc:
            status = "budget_stopped"
            print(f"   budget stop: {exc}")
        except Exception as exc:
            status = "failed"
            print(f"   run failed: {type(exc).__name__}: {str(exc)[:300]}")
        root_calls = engine.db.rows("SELECT COUNT(*) AS n FROM outcomes WHERE run_id=? AND "
                                    "agent_id=? AND phase='agent'", (run_id, f"{run_id}:root"))
        if status == "completed" and root_calls[0]["n"] >= limits["per_instance_step_limit"]:
            status = "step_limit"
        engine.end_run(status)
        return run_id, sandbox.patch()


def main(args):
    cfg = config_module.load(CONFIG_DIR).raw
    db_path = str(ROOT / cfg["storage"]["db_path"])
    ids = args.ids or cfg["instances"][args.set]
    instances = tasks.load(ids, cfg["instances"]["dataset"])
    out = ROOT / "data" / "runs" / args.label
    out.mkdir(parents=True, exist_ok=True)
    name = track(args.arm, args.jev)
    predictions = out / f"{name}.jsonl"
    run_ids = {}
    with predictions.open("w") as f:
        for inst in instances:
            print(f"-> {args.label} {name} {inst.instance_id}", flush=True)
            run_id, patch = run_instance(inst, args.arm, args.mode, args.label, cfg, db_path,
                                         args.jev)
            run_ids[inst.instance_id] = run_id
            f.write(json.dumps({"instance_id": inst.instance_id,
                                "model_name_or_path": f"deepagents-{name}",
                                "model_patch": patch}) + "\n")
            print(f"   {run_id}  patch {len(patch)} bytes  "
                  f"spent on label ${spent_on_label(db_path, args.label):.4f}", flush=True)
    if args.no_eval:
        return
    result = evaluate(predictions, list(run_ids), f"{args.label}-{name.replace('+', '-')}-{int(time.time())}",
                      cfg["instances"]["dataset"], workdir=out)
    db = AgentDB(db_path)
    for instance_id, run_id in run_ids.items():
        db.set_resolved(run_id, result["per_instance"][instance_id])
    print(f"   resolved: {result['resolved']}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--label", required=True, help="groups runs; budgets and reports are per label")
    p.add_argument("--arm", choices=["baseline", "econo"], required=True)
    p.add_argument("--mode", choices=["observe", "autopilot"], default="observe")
    p.add_argument("--set", choices=["dev", "comparison"], default="dev")
    p.add_argument("--ids", nargs="*", help="explicit instance ids instead of a configured set")
    p.add_argument("--no-eval", action="store_true", help="skip the official evaluation")
    p.add_argument("--jev", action="store_true",
                   help="econo arm only: planner asks Jev for p_need_again (default: fixed guess)")
    main(p.parse_args())
