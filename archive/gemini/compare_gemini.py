"""Compare arms of the full-control track: per task and arm, pass/fail, run cost, calls, rules fired;
totals and mean per arm. Jev's own cost is shown apart and never added to run cost.

Run: .venv/bin/python benchmarks/tblite/compare_gemini.py --label gx1 [--label gx2 ...]
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JEV_USD_PER_MTOK = 0.042  # unverified (no published price); kept apart from run cost


def load(labels):
    rows = []
    for label in labels:
        for path in sorted((ROOT / "runs" / label).glob("*/*/summary.json")):
            s = json.loads(path.read_text())
            arm, trial = path.parts[-3], path.parts[-2]
            task, repeat = trial.rsplit("-r", 1)
            rows.append({"label": label, "arm": arm, "task": task, "repeat": int(repeat), **s})
    return rows


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--label", action="append", required=True)
    p.add_argument("--means", action="store_true",
                   help="per-task averages over all repeats (arms may have different repeat counts)")
    a = p.parse_args()
    rows, replaced = [], []
    by_task = defaultdict(dict)
    for r in load(a.label):
        if r.get("exception") or r.get("reward") is None or r.get("skipped"):
            continue  # crashed, aborted or skipped trials never enter tables, totals or pairs
        old = by_task[(r["task"], r["repeat"])].get(r["arm"])
        if old and not old.get("exception"):
            continue  # a later label only replaces a trial the harness crashed on
        if old:
            replaced.append(f"{old['label']} {r['arm']} {r['task']} ({old['exception']}) -> {r['label']}")
            rows.remove(old)
        by_task[(r["task"], r["repeat"])][r["arm"]] = r
        rows.append(r)
    arms = sorted({r["arm"] for r in rows}, key=lambda x: (x != "raw", x))
    if a.means:
        return task_means(rows, arms)
    print(f"{'task':46s} " + "  ".join(f"{arm:>26s}" for arm in arms))
    for (task, repeat), cells in sorted(by_task.items()):
        parts = []
        for arm in arms:
            r = cells.get(arm)
            if not r or r.get("skipped"):
                parts.append(f"{'-':>26s}")
                continue
            ok = {1.0: "pass", 0.0: "fail"}.get(r.get("reward"), "err")
            parts.append(f"{ok:>5s} ${r['run_cost_usd']:.4f} {r['calls']:3d} calls")
        print(f"{task[:42] + f' r{repeat}':46s} " + "  ".join(f"{x:>26s}" for x in parts))
    print()
    for line in replaced:
        print(f"replaced (harness crash): {line}")
    for arm in arms:
        done = [r for r in rows if r["arm"] == arm and not r.get("skipped")]
        paired = [r for r in done if all(arm2 in by_task[(r["task"], r["repeat"])] for arm2 in arms)]
        cost = sum(r["run_cost_usd"] for r in paired)
        passed = sum(1 for r in paired if r.get("reward") == 1.0)
        rules, jev_in = defaultdict(int), 0
        for r in done:
            owner = r.get("owner") or {}
            for k, v in (owner.get("decisions") or {}).items():
                rules[k] += v
            jev_in += (owner.get("jev") or {}).get("input_tokens", 0)
        print(f"{arm:12s} paired trials {len(paired):2d}  passed {passed:2d}  run cost ${cost:.4f}  "
              f"mean ${cost / max(1, len(paired)):.4f}  Jev (separate) {jev_in:,} tokens "
              f"~${jev_in * JEV_USD_PER_MTOK / 1e6:.4f}")
        if rules:
            print("             rules: " + ", ".join(f"{k} x{v}" for k, v in sorted(rules.items())))


def task_means(rows, arms) -> None:
    """Per task: mean run cost and pass rate per arm over its repeats; then, for each arm, the
    paired comparison with raw on the tasks both ran (one pair per task: the two means)."""
    import math
    import statistics as st
    cell = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if not r.get("skipped"):
            cell[r["task"]][r["arm"]].append(r)
    print(f"{'task':40s} " + "  ".join(f"{arm:>24s}" for arm in arms))
    for task in sorted(cell):
        parts = []
        for arm in arms:
            runs = cell[task].get(arm, [])
            if not runs:
                parts.append(f"{'-':>24s}")
                continue
            cost = st.mean(r["run_cost_usd"] for r in runs)
            passed = sum(r.get("reward") == 1.0 for r in runs)
            parts.append(f"${cost:.4f} {passed}/{len(runs)} pass")
        print(f"{task[:40]:40s} " + "  ".join(f"{x:>24s}" for x in parts))
    print()
    for arm in arms[1:]:
        tasks = [t for t in cell if cell[t].get(arm) and cell[t].get("raw")]
        raw = [st.mean(r["run_cost_usd"] for r in cell[t]["raw"]) for t in tasks]
        eco = [st.mean(r["run_cost_usd"] for r in cell[t][arm]) for t in tasks]
        diffs = [e - r for e, r in zip(eco, raw)]
        line = f"{arm:20s} vs raw on {len(tasks)} tasks: raw ${sum(raw):.4f}  {arm} ${sum(eco):.4f}  " \
               f"change {100 * sum(diffs) / sum(raw):+.1f}%  cheaper on {sum(d < 0 for d in diffs)}/{len(diffs)}"
        if len(diffs) > 2 and st.stdev(diffs) > 0:
            line += f"  paired t = {st.mean(diffs) / (st.stdev(diffs) / math.sqrt(len(diffs))):.2f}"
        print(line)


if __name__ == "__main__":
    main()
