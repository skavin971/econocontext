"""How good were the edit quotes? The quoted bound R vs the re-read the edit caused.

  python -m econoclm.analysis.quote_check runs/<date>

For each applied edit in an EconoCLM run (econo.sqlite, table edits), on the model call
right after it (rereads.py, with the measured hidden-thinking accounting of
quote/hidden.py), the actual extra uncached tokens are split in two:

  edit-caused      = max(0, min(A, c_before) - max(c_after, P'))
                     cached tokens the edit put out of reach and that are not cached now
                     (P' = P, or 0 below Gemini's cache minimum: edit_quote.CACHE_MIN_TOKENS)
  post-edit misses = extra uncached - edit-caused
                     misses the edit cannot explain (Gemini's own cache misses)

P is the first changed position and A the start of the newly appended turns, in Gemini
tokens: exact when rewrite_positions.json exists (rewrite_positions.py: logged bodies +
countTokens), else the quote's own p and the call's prompt minus its appended tokens.

Reported: bound violations (edit-caused > R; R is shown to the model as an upper
bound), and R vs edit-caused only where the cache was healthy on both neighbouring
calls (both had cached tokens), plus per edit the seconds since the previous call.
"""

import argparse
import json
import statistics
from pathlib import Path

from ..quote.edit_quote import reachable_prefix
from .common import RETRY_REASONS, load_runs
from .rereads import call_rereads


def check(runs, count=None, exact: dict | None = None) -> list[dict]:
    rows = []
    for run in runs:
        store = run.store()
        if store is None:
            continue
        calls = [c for c in run.calls if c["finish_reason"] not in RETRY_REASONS]
        rr = {x["call"]: x for x in call_rereads(run.snapshots(), run.calls, count=count)}
        for e in store.rows("SELECT * FROM edits ORDER BY turn"):
            n = e["next_call"]
            actual = rr.get(n)
            row = {"run_id": run.run_id, "turn": e["turn"], "R": e["predicted_reprocess_R"],
                   "actual": actual["extra_uncached"] if actual else None,
                   "rewrite_seen": actual["rewrite"] if actual else None,
                   "gap_s": round(actual["gap_s"], 1) if actual and actual["gap_s"] is not None
                   else None}
            if actual and 1 <= n < len(calls):
                x = (exact or {}).get((run.run_id, n))
                c_before, c_after = calls[n - 1]["cached_tokens"] or 0, calls[n]["cached_tokens"] or 0
                P = x["P_exact"] if x else e["prefix_tokens_p"]
                A = x["A_exact"] if x else actual["prompt"] - actual["appended"]
                edit_caused = max(0, min(A, c_before) - max(c_after, reachable_prefix(P or 0)))
                row.update(edit_caused=edit_caused,
                           post_edit_miss=actual["extra_uncached"] - edit_caused,
                           violation=row["R"] is not None and edit_caused > row["R"],
                           healthy=c_before > 0 and c_after > 0, exact=x is not None)
            rows.append(row)
    return rows


def summary(rows: list[dict]) -> dict:
    split = [r for r in rows if "edit_caused" in r]
    healthy = [r for r in split if r["healthy"] and r["R"] is not None]
    errs = [abs(r["R"] - r["edit_caused"]) for r in healthy]
    pct = [abs(r["R"] - r["edit_caused"]) / r["R"] for r in healthy if r["R"] > 0]
    return {"edits": len(rows), "compared": len(split),
            "violations": sum(r["violation"] for r in split),
            "healthy": len(healthy),
            "median_abs_err_tokens": statistics.median(errs) if errs else None,
            "median_abs_err_pct_of_R": statistics.median(pct) if pct else None,
            "edit_caused": sum(r["edit_caused"] for r in split),
            "post_edit_miss": sum(r["post_edit_miss"] for r in split),
            "exact": sum(r["exact"] for r in split)}


def report(rows: list[dict]) -> str:
    arms = sorted({r["run_id"].split("-", 1)[0] for r in rows})
    if len(arms) > 1:
        return "\n".join(report([r for r in rows if r["run_id"].split("-", 1)[0] == a])
                          .replace("# Quote accuracy", f"# Quote accuracy: {a}", 1) for a in arms)
    s = summary(rows)
    pct = "n/a" if s["median_abs_err_pct_of_R"] is None else f"{s['median_abs_err_pct_of_R']:.1%}"
    lines = ["# Quote accuracy", "",
             f"Edits: {s['edits']}, compared with the next call: {s['compared']} "
             f"({s['exact']} with exact positions)",
             f"Bound violations (edit-caused re-read > R): {s['violations']}",
             f"Actual re-read split: edit-caused {s['edit_caused']:,} tokens, post-edit misses "
             f"{s['post_edit_miss']:,}",
             f"Healthy cache on both neighbouring calls: {s['healthy']}; there median "
             f"|R - edit-caused| {s['median_abs_err_tokens']} tokens ({pct} of R)", "",
             "| Run | Turn | R (bound) | Edit-caused | Post-edit misses | Violation | Healthy | "
             "Seconds since previous call |",
             "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['run_id']} | {r['turn']} | {r['R']} | {r.get('edit_caused')} | "
                     f"{r.get('post_edit_miss')} | {'YES' if r.get('violation') else ''} | "
                     f"{'yes' if r.get('healthy') else ''} | {r['gap_s']} |")
    return "\n".join(lines) + "\n"


def load_exact(day: Path) -> dict | None:
    path = day / "rewrite_positions.json"
    if not path.exists():
        return None
    return {(x["run"], x["call"]): x for x in json.loads(path.read_text())}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("day", type=Path)
    args = ap.parse_args()
    runs = [r for r in load_runs(args.day) if r.arm != "raw"]   # every EconoCLM arm
    text = report(check(runs, exact=load_exact(args.day)))
    (args.day / "quote_check.md").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
