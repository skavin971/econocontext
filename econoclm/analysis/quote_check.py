"""How good were the edit quotes? Predicted R vs the actual re-read on the next call.

  python -m econoclm.analysis.quote_check runs/<date>

For each applied edit in an EconoCLM run (econo.sqlite, table edits): the quote's
predicted R, and the actual extra_uncached of the model call right after the edit
(rereads.py). Reports the median absolute error in tokens and as a % of R.
"""

import argparse
import statistics
from pathlib import Path

from .common import load_runs
from .rereads import call_rereads


def check(runs, count=None) -> list[dict]:
    rows = []
    for run in runs:
        store = run.store()
        if store is None:
            continue
        rr = {x["call"]: x for x in call_rereads(run.snapshots(), run.calls, count=count)}
        for e in store.rows("SELECT * FROM edits ORDER BY turn"):
            actual = rr.get(e["next_call"])
            rows.append({"run_id": run.run_id, "turn": e["turn"], "R": e["predicted_reprocess_R"],
                         "actual": actual["extra_uncached"] if actual else None,
                         "rewrite_seen": actual["rewrite"] if actual else None})
    return rows


def summary(rows: list[dict]) -> dict:
    pairs = [(r["R"], r["actual"]) for r in rows if r["R"] is not None and r["actual"] is not None]
    errs = [abs(p - a) for p, a in pairs]
    pct = [abs(p - a) / p for p, a in pairs if p > 0]
    return {"edits": len(rows), "compared": len(pairs),
            "median_abs_err_tokens": statistics.median(errs) if errs else None,
            "median_abs_err_pct_of_R": statistics.median(pct) if pct else None}


def report(rows: list[dict]) -> str:
    s = summary(rows)
    pct = "n/a" if s["median_abs_err_pct_of_R"] is None else f"{s['median_abs_err_pct_of_R']:.1%}"
    lines = ["# Quote accuracy", "",
             f"Edits: {s['edits']}, compared with the next call: {s['compared']}",
             f"Median absolute error: {s['median_abs_err_tokens']} tokens ({pct} of R)", "",
             "| Run | Turn | Predicted R | Actual extra uncached | Rewrite seen |", "|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['run_id']} | {r['turn']} | {r['R']} | {r['actual']} | {r['rewrite_seen']} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("day", type=Path)
    args = ap.parse_args()
    text = report(check(load_runs(args.day, {"econo"})))
    (args.day / "quote_check.md").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
