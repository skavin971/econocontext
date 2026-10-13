"""Step 15: the report's tables, from runs/tier-a/step15/{runs,edits}.csv (step15_placement.py).
FIXED-TRAJECTORY ESTIMATES on the canonical track. Writes runs/tier-a/step15/summary.md.
Use: .venv/bin/python runs/tier-a/scripts/step15_summary.py"""

import csv
from collections import Counter
from pathlib import Path

D = Path("runs/tier-a/step15")
runs = list(csv.DictReader((D / "runs.csv").open()))
edits = list(csv.DictReader((D / "edits.csv").open()))
f = lambda r, k: float(r[k] or 0)   # noqa: E731
pct = lambda a, z: f"{100 * a / z:.1f}%" if z else "–"   # noqa: E731
PLANS = (("a", "(a) as executed"), ("a_inplace", "(a') in place, formatting kept"), ("b", "(b) tail"),
         ("c", "(c) batched"), ("d", "(d) cost-planned"))
out = ["# Step 15 tables (fixed-trajectory estimates, canonical track)", ""]


def concentration(values) -> str:
    v = sorted((x for x in values if x > 0), reverse=True)
    total, acc = sum(v), 0.0
    for n, x in enumerate(v, 1):
        acc += x
        if acc >= 0.8 * total:
            return f"{n} of {len(list(values))} runs give 80%"
    return "–"


def block(title: str, rs: list[dict]) -> None:
    if not rs:
        return
    Fa = sum(f(r, "F_a") for r in rs)
    tot = {k: sum(f(r, f"F_{k}") for r in rs) for k, _ in PLANS}
    simple = min(("a", "b", "c"), key=lambda k: tot[k])
    names = dict(PLANS)
    out.append(f"\n### {title}: {len(rs)} runs, {sum(f(r, 'calls') for r in rs):,.0f} calls, "
               f"{sum(f(r, 'edits') for r in rs):,.0f} edits ({sum(f(r, 'rollbacks') for r in rs):,.0f} rollbacks); "
               f"runs with an edit: {sum(f(r, 'edits') > 0 for r in rs)}\n")
    out.append(f"Piece model vs the real canonical track, plan (a): prompt tokens {pct(sum(f(r, 'model_P') for r in rs), sum(f(r, 'real_P') for r in rs))}, "
               f"FLOPs {pct(Fa, sum(f(r, 'F_real') for r in rs))}.\n")
    out.append("| plan | PFLOPs | share of (a) |")
    out.append("|---|---|---|")
    for k, name in PLANS:
        out.append(f"| {name} | {tot[k] / 1e15:.2f} | **{pct(tot[k], Fa)}** |")
    out.append(f"\nAttribution of (b): the tail rule at rollbacks only (in place elsewhere) {pct(sum(f(r, 'F_b_rollbacks') for r in rs), Fa)}; "
               f"at context edits only {pct(sum(f(r, 'F_b_context_edits') for r in rs), Fa)}.")
    gap = tot[simple] - tot["d"]
    out.append(f"\nBest simple rule: {names[simple]}. Gap to (d): {pct(gap, Fa)} of (a) "
               f"({gap / 1e15:.2f} PFLOPs; {concentration([f(r, f'F_{simple}') - f(r, 'F_d') for r in rs])}).")
    ex = [r for r in rs if r["F_exhaustive"] != ""]
    ex_e = [r for r in ex if f(r, "edits") > 0]
    if ex_e:
        out.append(f"Exhaustive check (runs with 1-6 edits: {len(ex_e)}): (d) is {pct(sum(f(r, 'F_d') for r in ex_e), sum(f(r, 'F_exhaustive') for r in ex_e))} "
                   f"of the true optimum; runs where (d) misses it by more than 0.1%: "
                   f"{sum(f(r, 'F_d') > 1.001 * f(r, 'F_exhaustive') for r in ex_e)}.")
    rr = [r for r in rs if r["F_restarts"] != ""]
    if rr:
        out.append(f"Random-restart check (runs with 7+ edits: {len(rr)}; 20 seeded restarts of the descent): best found is "
                   f"{pct(sum(min(f(r, 'F_restarts'), f(r, 'F_d')) for r in rr), sum(f(r, 'F_d') for r in rr))} of (d); runs where it "
                   f"beats (d) by more than 0.1%: {sum(f(r, 'F_restarts') < 0.999 * f(r, 'F_d') for r in rr)}.")
    calls = sum(f(r, "calls") for r in rs)
    ec = sum(f(r, "c_extra_calls") for r in rs)
    out.append(f"(c) batched: removed tokens stay visible on {ec:,.0f} of {calls:,.0f} calls ({pct(ec, calls)}), "
               f"{sum(f(r, 'c_extra_tokens') for r in rs) / max(ec, 1):,.0f} extra tokens per such call on average; "
               f"{sum(f(r, 'c_flushes') for r in rs):,.0f} batched applications. (d): extra tokens visible on "
               f"{sum(f(r, 'd_extra_calls') for r in rs):,.0f} calls.")
    ids = {r["run_id"] for r in rs}
    es = [e for e in edits if e["run_id"] in ids]
    by = Counter((e["type"], e["d_choice"]) for e in es)
    types = sorted({e["type"] for e in es})
    if es:
        out.append("\n| edit type | edits | (d) in place | (d) tail | (d) deferred | median first change, tokens (content only) | added tokens | of which reasoning | removed tokens |")
        out.append("|---|---|---|---|---|---|---|---|---|")
        for t in types:
            xs = [e for e in es if e["type"] == t]
            fc = sorted(f(e, "first_change_tokens") for e in xs)
            fcc = sorted(f(e, "first_content_change_tokens") for e in xs)
            out.append(f"| {t} | {len(xs)} | {by[(t, 'inplace')]} | {by[(t, 'tail')]} | {by[(t, 'defer')]} | {fc[len(fc) // 2]:,.0f} ({fcc[len(fcc) // 2]:,.0f}) | "
                       f"{sum(f(e, 'added_tokens') for e in xs):,.0f} | {sum(f(e, 'added_reasoning_tokens') for e in xs):,.0f} | "
                       f"{sum(f(e, 'removed_tokens') for e in xs):,.0f} |")
    na = sum(f(r, "F_nors_a") for r in rs)
    nt = {k: sum(f(r, f"F_nors_{k}") for r in rs) for k in ("a", "b", "c", "d")}
    nbest = min(("a", "b", "c"), key=lambda k: nt[k])
    out.append(f"\nWithout the reasoning made visible (the same runs, those pieces removed from every prompt; (a) = {na / 1e15:.2f} PFLOPs): "
               f"(b) {pct(nt['b'], na)}, (c) {pct(nt['c'], na)}, (d) {pct(nt['d'], na)} of its (a); best simple rule "
               f"{names[nbest]}, gap to (d) {pct(nt[nbest] - nt['d'], na)}.")
    rs_cost = [f(r, "F_a") - f(r, "F_no_reasoning") for r in rs]
    with_rs = [r for r in rs if f(r, "reasoning_tokens") > 0]
    out.append(f"\nReasoning made visible (plan (a)): {pct(sum(rs_cost), Fa)} of FLOPs; in {len(with_rs)} of {len(rs)} runs "
               f"({len([r for r in rs if f(r, 'edits') > 0])} runs have an edit); {sum(f(r, 'reasoning_tokens') for r in rs):,.0f} tokens; "
               f"{concentration(rs_cost)}; largest single run {pct(max(rs_cost), Fa)} of the group's FLOPs.")


for bench, name in (("tblite", "TBLite"), ("bcp", "BrowseComp-Plus pilot")):
    rs = [r for r in runs if r["bench"] == bench]
    out.append(f"\n## {name}")
    block("All CLM runs", rs)
    if bench == "tblite":
        for arm in sorted({r["arm"] for r in rs}):
            block(f"arm {arm}", [r for r in rs if r["arm"] == arm])
(D / "summary.md").write_text("\n".join(out) + "\n")
print("\n".join(out))
