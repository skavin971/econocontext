"""The edit ceiling: what share of the bill is caused by edits forcing re-reads.

  python -m econoclm.analysis.edit_ceiling runs/<date> [--arms raw,econo]

Per arm: sum over calls that follow a rewrite of extra_uncached x ($0.75 - $0.075)/1M,
divided by the arm's total $ (rereads.py has the definitions). For comparison it
also reports the same quantity over append-only calls (the background: Gemini's
random cache misses), which an edit policy cannot remove.

If the ceiling is about 10% of the bill or more, cache-aware commits (v2) are worth
building; below that, they cannot save much.
"""

import argparse
from collections import defaultdict
from pathlib import Path

from .common import load_runs
from .rereads import call_rereads


def ceiling(runs, count=None) -> dict:
    per_arm = defaultdict(lambda: {"cost": 0.0, "edit_usd": 0.0, "background_usd": 0.0,
                                   "rewrites": 0, "calls": 0, "runs": 0, "unmatched": 0})
    per_run = []
    for run in runs:
        snaps = run.snapshots()
        rr = call_rereads(snaps, run.calls, count=count)
        a = per_arm[run.arm]
        cost = sum(r["cost_usd"] or 0 for r in run.all_rows)
        edit = sum(x["extra_usd"] for x in rr if x["rewrite"])
        background = sum(x["extra_usd"] for x in rr if not x["rewrite"])
        a["cost"] += cost
        a["edit_usd"] += edit
        a["background_usd"] += background
        a["rewrites"] += sum(x["rewrite"] for x in rr)
        a["calls"] += len(run.calls)
        a["runs"] += 1
        a["unmatched"] += int(len(snaps) != len(run.calls))
        per_run.append({"run_id": run.run_id, "cost": cost, "edit_usd": edit,
                        "background_usd": background, "rewrites": sum(x["rewrite"] for x in rr),
                        "snapshots": len(snaps), "calls": len(run.calls)})
    return {"arms": dict(per_arm), "runs": per_run}


def report(res: dict) -> str:
    lines = ["# Edit ceiling", "",
             "| Arm | Runs | Calls | Rewrites | Total $ | Re-read $ after rewrites | "
             "Edit ceiling | Background (append-only) | Snapshot/call mismatches |",
             "|---|---|---|---|---|---|---|---|---|"]
    for arm, a in sorted(res["arms"].items()):
        share = a["edit_usd"] / a["cost"] if a["cost"] else 0
        bg = a["background_usd"] / a["cost"] if a["cost"] else 0
        lines.append(f"| {arm} | {a['runs']} | {a['calls']} | {a['rewrites']} | "
                     f"${a['cost']:.4f} | ${a['edit_usd']:.4f} | {share:.1%} | {bg:.1%} | "
                     f"{a['unmatched']} |")
    lines += ["", "## Per run", "",
              "| Run | Calls | Snapshots | Rewrites | $ | Re-read $ after rewrites | Share |",
              "|---|---|---|---|---|---|---|"]
    for r in res["runs"]:
        share = r["edit_usd"] / r["cost"] if r["cost"] else 0
        lines.append(f"| {r['run_id']} | {r['calls']} | {r['snapshots']} | {r['rewrites']} | "
                     f"${r['cost']:.4f} | ${r['edit_usd']:.4f} | {share:.1%} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("day", type=Path)
    ap.add_argument("--arms", default=None)
    args = ap.parse_args()
    arms = set(args.arms.split(",")) if args.arms else None
    text = report(ceiling(load_runs(args.day, arms)))
    (args.day / "edit_ceiling.md").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
