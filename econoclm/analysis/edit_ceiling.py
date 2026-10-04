"""The edit ceiling: what share of the bill is caused by edits forcing re-reads.

  python -m econoclm.analysis.edit_ceiling runs/<date> [--arms raw,econo]

Per arm, in dollars at (input - cached) price per re-read token, as a share of the arm's
total $ (rereads.py and rewrite_positions.py have the definitions):

  edit ceiling       the EDIT-CAUSED re-read on calls that follow a rewrite: cached tokens
                     the edit put out of reach and that are not cached now. Split in three:
    lost prefix      the unchanged prefix, when it is below Gemini's cache minimum
                     (edit_quote.CACHE_MIN_TOKENS): the cache then serves none of it
    format change    CLM rewriting still-structured turns as plain text (from the first
                     turn added since the previous edit up to the turn the model edited)
    edit position    re-read from the edited turn onward
  post-edit misses   the rest of the re-read after a rewrite: Gemini's own misses
  append-only misses re-read on calls that do NOT follow a rewrite: Gemini's own misses
                     (the newest text not yet cached, or older prefix not served)

The edit-caused split needs exact positions (rewrite_positions.json, made from the logged
bodies with countTokens). Without it, all re-read after a rewrite counts as edit-caused,
as before, and the report says so.

Only the edit-position part responds to WHERE the model edits; the lost prefix responds
to keeping the unchanging prefix above the cache minimum. If the ceiling is about 10% of
the bill or more, cache-aware commits (v2) are worth building; below that, they cannot
save much.
"""

import argparse
from collections import defaultdict
from pathlib import Path

from ..core import prices
from .common import load_runs
from .quote_check import load_exact
from .rereads import call_rereads

PRICE_GAP = prices.PRICE_IN - prices.PRICE_CACHED


def ceiling(runs, count=None, exact: dict | None = None) -> dict:
    keys = ("cost", "edit_usd", "lost_prefix_usd", "format_usd", "edit_pos_usd",
            "post_edit_usd", "append_only_usd")
    per_arm = defaultdict(lambda: {**{k: 0.0 for k in keys}, "rewrites": 0, "calls": 0,
                                   "runs": 0, "unmatched": 0, "exact": 0})
    per_run = []
    for run in runs:
        snaps = run.snapshots()
        rr = call_rereads(snaps, run.calls, count=count)
        r = {k: 0.0 for k in keys}
        r["cost"] = sum(x["cost_usd"] or 0 for x in run.all_rows)
        n_exact = 0
        for x in rr:
            if not x["rewrite"]:
                r["append_only_usd"] += x["extra_usd"]
                continue
            e = (exact or {}).get((run.run_id, x["call"]))
            if e:
                n_exact += 1
                caused, lost = e["edit_caused"], e["lost_prefix"]
                fmt = min(caused - lost, x["format_uncached"])
                r["lost_prefix_usd"] += lost * PRICE_GAP
                r["format_usd"] += fmt * PRICE_GAP
                r["edit_pos_usd"] += (caused - lost - fmt) * PRICE_GAP
                r["edit_usd"] += caused * PRICE_GAP
                r["post_edit_usd"] += (x["extra_uncached"] - caused) * PRICE_GAP
            else:                      # no exact positions: everything counts as edit-caused
                r["format_usd"] += x["format_usd"]
                r["edit_pos_usd"] += x["edit_pos_usd"]
                r["edit_usd"] += x["extra_usd"]
        rewrites = sum(x["rewrite"] for x in rr)
        a = per_arm[run.arm]
        for k in keys:
            a[k] += r[k]
        a["rewrites"] += rewrites
        a["calls"] += len(run.calls)
        a["runs"] += 1
        a["unmatched"] += int(len(snaps) != len(run.calls))
        a["exact"] += n_exact
        per_run.append({"run_id": run.run_id, **r, "rewrites": rewrites, "exact": n_exact,
                        "snapshots": len(snaps), "calls": len(run.calls)})
    return {"arms": dict(per_arm), "runs": per_run}


def report(res: dict) -> str:
    lines = ["# Edit ceiling", "",
             "| Arm | Runs | Calls | Rewrites (exact) | Total $ | Edit-caused $ | Edit ceiling | "
             "of which: lost prefix | format change | edit position | Post-edit misses | "
             "Append-only misses | Snapshot/call mismatches |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for arm, a in sorted(res["arms"].items()):
        share = lambda x: x / a["cost"] if a["cost"] else 0  # noqa: E731
        lines.append(f"| {arm} | {a['runs']} | {a['calls']} | {a['rewrites']} ({a['exact']}) | "
                     f"${a['cost']:.4f} | ${a['edit_usd']:.4f} | {share(a['edit_usd']):.1%} | "
                     f"{share(a['lost_prefix_usd']):.1%} | {share(a['format_usd']):.1%} | "
                     f"{share(a['edit_pos_usd']):.1%} | {share(a['post_edit_usd']):.1%} | "
                     f"{share(a['append_only_usd']):.1%} | {a['unmatched']} |")
    if any(a["exact"] < a["rewrites"] for a in res["arms"].values()):
        lines += ["", "Rewrites without exact positions (no rewrite_positions.json entry): all of "
                      "their re-read is counted as edit-caused."]
    lines += ["", "## Per run", "",
              "| Run | Calls | Snapshots | Rewrites | $ | Edit-caused $ | Share | Lost prefix | "
              "Format change | Edit position | Post-edit misses | Append-only misses |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in res["runs"]:
        share = lambda x: x / r["cost"] if r["cost"] else 0  # noqa: E731
        lines.append(f"| {r['run_id']} | {r['calls']} | {r['snapshots']} | {r['rewrites']} | "
                     f"${r['cost']:.4f} | ${r['edit_usd']:.4f} | {share(r['edit_usd']):.1%} | "
                     f"{share(r['lost_prefix_usd']):.1%} | {share(r['format_usd']):.1%} | "
                     f"{share(r['edit_pos_usd']):.1%} | {share(r['post_edit_usd']):.1%} | "
                     f"{share(r['append_only_usd']):.1%} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("day", type=Path)
    ap.add_argument("--arms", default=None)
    args = ap.parse_args()
    arms = set(args.arms.split(",")) if args.arms else None
    text = report(ceiling(load_runs(args.day, arms), exact=load_exact(args.day)))
    (args.day / "edit_ceiling.md").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
