"""Side-by-side report for one label: baseline vs EconoContext, per instance.

    .venv/bin/python bench/report.py --label check1

This is a PIPELINE CHECK: it shows that the whole path runs and is measured. It
makes no savings claims. Token counts are always shown. Cost (NU and USD) appears
once econocontext/pricing/ledger.py is built; until then it prints "not built".
Runs with Jev on are reported apart from runs without it.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from econocontext import config as config_module  # noqa: E402
from econocontext.pricing.ledger import summary  # noqa: E402
from econocontext.store.db import AgentDB  # noqa: E402

WATCH = ("POINTER", "RETRIEVE_FROM_STORE", "ANSWER_FROM_STORE", "REUSE_RESULT")


def fmt(x, digits=0):
    return "-" if x is None else f"{x:,.{digits}f}"


def main(label: str) -> None:
    cfg = config_module.load(ROOT / "config")
    db = AgentDB(ROOT / cfg.raw["storage"]["db_path"])
    runs = db.rows("SELECT run_id FROM runs WHERE run_id LIKE ? ORDER BY instance_id, arm, "
                   "started_at", (f"{label}:%",))
    print(f"PIPELINE CHECK '{label}': a working, measured pipeline. Not an effectiveness comparison.\n")
    totals: dict[str, dict] = {}
    for r in runs:
        s = summary(db, r["run_id"])
        run, t = s["run"], s["totals"]
        track = f"{run['arm']}{'+jev' if run.get('jev') else ''}"
        applied = {}
        for d in s["decisions"]:
            key = f"{d['chosen']}{'*' if d['applied'] else ''}"
            applied[key] = applied.get(key, 0) + d["n"]
        pva = s["predicted_vs_actual"]
        print(f"{run['instance_id'] or run['run_id']}  [{track}/{run['mode']}]  status={run['status']}  "
              f"resolved={'-' if run['resolved'] is None else bool(run['resolved'])}")
        print(f"  calls {t['calls']} (compaction {t['compaction_calls']})  uncached {fmt(t['uncached_input'])}"
              f"  cache_read {fmt(t['cache_read'])}  cache_write {fmt(t['cache_write'])}"
              f"  output {fmt(t['output'])} (reasoning {fmt(t['reasoning'])})")
        share = t["cache_read_share"]
        cost = ("not built (pricing/ledger.py)" if t["cost_usd"] is None
                else f"{fmt(t['cost_nu'])} NU  ${t['cost_usd']:.4f}")
        print(f"  cost {cost}   cache-read share {'-' if share is None else f'{100 * share:.0f}%'}")
        print(f"  predicted vs actual (NU): {fmt(pva['predicted_call_nu'])} vs "
              f"{fmt(pva['actual_call_nu'])} over {pva['calls']} calls")
        if applied:
            print(f"  decisions (chosen; * = applied): {dict(sorted(applied.items()))}")
        feasible = {k: v for k, v in s["feasible_counts"].items() if k in WATCH}
        if feasible:
            print(f"  would-be (passed every gate): {feasible}")
        agg = totals.setdefault(track, dict(runs=0, resolved=0, nu=0.0, usd=0.0, calls=0))
        agg["runs"] += 1
        agg["resolved"] += int(run["resolved"] or 0)
        agg["nu"] += t["cost_nu"] or 0
        agg["usd"] += t["cost_usd"] or 0
        agg["calls"] += t["calls"] or 0
        print()
    for arm, a in totals.items():
        print(f"{arm:9s} runs {a['runs']}  resolved {a['resolved']}  calls {a['calls']}  "
              f"{fmt(a['nu'])} NU  ${a['usd']:.4f}  (USD comes from pricing/ledger.py)")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--label", required=True)
    main(p.parse_args().label)
