"""Step 16: the report's tables, from runs/tier-a/step16/{runs,cases}.csv (step16_waste.py).
FIXED-TRAJECTORY ESTIMATES on the canonical track (step 15's piece model). Writes runs/tier-a/step16/summary.md.
Use: .venv/bin/python runs/tier-a/scripts/step16_summary.py"""

import csv
from collections import Counter
from pathlib import Path

D = Path("runs/tier-a/step16")
runs = list(csv.DictReader((D / "runs.csv").open()))
cases = list(csv.DictReader((D / "cases.csv").open())) if (D / "cases.csv").exists() else []
f = lambda r, k: float(r[k] or 0)   # noqa: E731
pct = lambda a, z: f"{100 * a / z:.1f}%" if z else "–"   # noqa: E731
BENCH = (("tblite", "TBLite"), ("bcp", "BrowseComp-Plus pilot"))
GROUPS = (("clm", "CLM"), ("contrast", "our harness, no context management"))
WASTE = [("a", "(a) repeated actions, identical result"), ("a_visible", "  earlier result still visible (model behaviour)"),
         ("a_removed_edit", "  earlier result removed by CLM's edit (context manager)"),
         ("a_removed_rollback", "  earlier result removed by CLM's rollback (context manager)"),
         ("b", "(b) work redone after a rollback (context manager)"), ("c", "(c) turns after sufficiency"),
         ("d", "(d) runs ended without an answer"), ("e", "(e) unreferenced observations"),
         ("total", "**overlap-free total, (a)-(e)**"), ("f", "(f) replies cut at the output cap, thinking only (not in your list)"),
         ("total_with_f", "**overlap-free total, (a)-(f)**"), ("b_near", "sensitivity: rephrased redo after a rollback")]


def group(bench, g):
    return [r for r in runs if r["bench"] == bench and (r["arm"].startswith("clm") == (g == "clm"))]


out = ["# Step 16 tables (fixed-trajectory estimates, canonical track)", "",
       "## Waste as a share of each group's total FLOPs", "",
       "| category | " + " | ".join(f"{bn}: {gn}" for _, bn in BENCH for _, gn in GROUPS) + " |",
       "|---|" + "---|" * 4]
for key, label in WASTE:
    cells = []
    for bench, _ in BENCH:
        for g, _ in GROUPS:
            rs = group(bench, g)
            F = sum(f(r, "F") for r in rs)
            cells.append(pct(sum(f(r, f"W_{key}") for r in rs), F))
    out.append(f"| {label} | " + " | ".join(cells) + " |")
out.append("| runs; PFLOPs | " + " | ".join(f"{len(group(bn, g))}; {sum(f(r, 'F') for r in group(bn, g)) / 1e15:.1f}"
                                         for bn, _ in BENCH for g, _ in GROUPS) + " |")

for bench, bname in BENCH:
    for g, gname in GROUPS:
        rs = group(bench, g)
        if not rs:
            continue
        F = sum(f(r, "F") for r in rs)
        ids = {r["run_id"] for r in rs}
        cs = [c for c in cases if c["run_id"] in ids]
        out += ["", f"## {bname}, {gname}: {len(rs)} runs, {sum(f(r, 'calls') for r in rs):,.0f} calls, {F / 1e15:.1f} PFLOPs", "",
                f"Piece model vs the real prompts: {pct(sum(f(r, 'model_P') for r in rs), sum(f(r, 'real_P') for r in rs))} of the tokens.", "",
                "### Waterfall (parts sum to 100%)", "", "| part | share of FLOPs |", "|---|---|"]
        parts = [k for k in rs[0] if k.startswith("P_")]
        total = sum(f(r, k) for r in rs for k in parts)
        for k in parts:
            v = sum(f(r, k) for r in rs)
            if v:
                out.append(f"| {k[2:]} | {pct(v, F)} |")
        out.append(f"| (sum) | {pct(total, F)} |")
        a_cases = [c for c in cs if c["category"] == "a"]
        st = Counter((c["status"], c["identical"]) for c in a_cases)
        out += ["", "### Counts", ""]
        out.append(f"- (a) repeats of the same normalized action: {len(a_cases)}; identical result {sum(c['identical'] == 'True' for c in a_cases)} "
                   f"(visible {st[('visible', 'True')]}, removed by edit {st[('removed by edit', 'True')]}, removed by rollback "
                   f"{st[('removed by rollback', 'True')]}); result changed (not counted) {sum(c['identical'] == 'False' for c in a_cases)}.")
        out.append(f"- (b) rollbacks {sum(f(r, 'rollbacks') for r in rs):,.0f}; exact redos within 5 calls {sum(f(r, 'redo_calls') for r in rs):,.0f}; "
                   f"rephrased redos (sensitivity) {sum(f(r, 'near_redo_calls') for r in rs):,.0f}.")
        suff = [r for r in rs if r["sufficiency_call"] != ""]
        out.append(f"- (c) runs with a sufficiency point: {len(suff)} of {len(rs)}; calls after it: {sum(f(r, 'after_calls') for r in rs):,.0f} "
                   f"(sufficiency calls: " + ", ".join(f"{r['run_id'].split('.')[-2] if bench == 'bcp' else r['run_id']} at {r['sufficiency_call']} of {r['calls']}"
                                                      for r in suff if f(r, "after_calls") > 0) + ").")
        out.append(f"- (d) runs without an answer: {sum(r['no_answer'] == 'True' for r in rs)} of {len(rs)}.")
        obs = sum(f(r, "obs_tokens") for r in rs)
        out.append(f"- (e) unreferenced observations: {sum(f(r, 'unref_outputs') for r in rs):,.0f} of {sum(f(r, 'outputs') for r in rs):,.0f} outputs, "
                   f"{pct(sum(f(r, 'unref_tokens') for r in rs), obs)} of observation tokens"
                   + (f"; per document inside search results: {pct(sum(f(r, 'snippet_unref_tokens') for r in rs), sum(f(r, 'snippet_tokens') for r in rs))} "
                      f"of snippet tokens unreferenced" if bench == "bcp" else "") + ".")
        out.append(f"- (f) replies cut at the output cap with thinking only: {sum(f(r, 'cut_calls') for r in rs):,.0f} calls, "
                   f"{sum(f(r, 'cut_G') for r in rs):,.0f} generated tokens.")
(D / "summary.md").write_text("\n".join(out) + "\n")
print("\n".join(out))
