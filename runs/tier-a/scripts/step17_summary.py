"""Step 17: the report's tables, from runs/tier-a/step17/{runs,cut}.csv (step17_policies.py).
FIXED-TRAJECTORY ESTIMATES on the canonical track. Writes runs/tier-a/step17/summary.md.
Use: .venv/bin/python runs/tier-a/scripts/step17_summary.py"""

import csv
import statistics
from pathlib import Path

D = Path("runs/tier-a/step17")
runs = list(csv.DictReader((D / "runs.csv").open()))
cuts = list(csv.DictReader((D / "cut.csv").open())) if (D / "cut.csv").exists() else []
f = lambda r, k: float(r[k] or 0)   # noqa: E731
pct = lambda a, z: f"{100 * a / z:.1f}%" if z else "–"   # noqa: E731
POLICIES = ["P0", "P1", "P2", "P3", "P4 a=1 simple", "P4 a=1 oracle", "P4 a=2 simple", "P4 a=2 oracle", "P5"]
BUDGETED = POLICIES[2:]          # under the budget by construction (P0 has none; P1 is what ran)
SOURCES = [("tblite", "TBLite, all arms"), ("bcp-clm", "BrowseComp-Plus, CLM pilot"),
           ("bcp-gpt5", "BrowseComp-Plus, released GPT-5 BM25 runs"), ("bcp-base", "BrowseComp-Plus, our base pilot (contrast)")]
out = ["# Step 17 tables (fixed-trajectory estimates, canonical track)", "",
       "Shares are of P0 (keep everything) on the same traces. Dollars: uncached input 1, cache read 0.1, output 6 per token.", ""]
lead = []
for src, name in SOURCES:
    rs = [r for r in runs if r["source"] == src]
    if not rs:
        continue
    tot = {p: {k: sum(f(r, f"{p}|{k}") for r in rs) for k in ("F", "U", "sent", "dollars", "restores", "compactions", "over")}
           for p in POLICIES}
    F0, D0 = tot["P0"]["F"], tot["P0"]["dollars"]
    calls = sum(f(r, "calls") for r in rs)
    out += [f"## {name}: {len(rs)} traces, {calls:,.0f} calls; observations {sum(f(r, 'observations') for r in rs):,.0f}, "
            f"needed again {sum(f(r, 'obs_needed_again') for r in rs):,.0f}; outputs never shown in full (not in the trace) "
            f"{sum(f(r, 'missing_outputs') for r in rs):,.0f}", "",
            "| policy | FLOPs (share of P0) | dollars (share of P0) | new-prefill tokens | tokens sent | restores | budget compactions | calls over the limit |",
            "|---|---|---|---|---|---|---|---|"]
    for p in POLICIES:
        t = tot[p]
        out.append(f"| {p} | {pct(t['F'], F0)} | {pct(t['dollars'], D0)} | {t['U'] / 1e6:.2f}M | {t['sent'] / 1e6:.2f}M | "
                   f"{t['restores']:,.0f} | {t['compactions']:,.0f} | {t['over']:,.0f} |")
    out.append(f"\nP0: {F0 / 1e15:.1f} PFLOPs, {D0 / 1e6:.2f}M input-token dollars.")
    rank_F = sorted(BUDGETED, key=lambda p: tot[p]["F"])
    rank_D = sorted(BUDGETED, key=lambda p: tot[p]["dollars"])
    best_F, best_D = rank_F[0], rank_D[0]
    out.append(f"Ranking of the budgeted policies (P2-P5) by FLOPs: {' < '.join(rank_F)}.")
    out.append(f"By dollars: {' < '.join(rank_D)}.")
    for p in ("P4 a=1 simple", "P4 a=2 simple", "P5"):
        out.append(f"- {p}: {100 * (tot[p]['F'] - tot[best_F]['F']) / F0:+.1f} points of P0 FLOPs from the best ({best_F}); "
                   f"{100 * (tot[p]['dollars'] - tot[best_D]['dollars']) / D0:+.1f} points of P0 dollars from the best ({best_D}).")
    out.append("")
    lead.append((name, tot, best_F, best_D, rank_F, rank_D))

out += ["## Lead", "", "| traces | P2 vs P0: FLOPs | P2 vs P0: dollars | best by FLOPs | best by dollars | ranking flips? |", "|---|---|---|---|---|---|"]
for name, tot, bF, bD, rF, rD in lead:
    F0, D0 = tot["P0"]["F"], tot["P0"]["dollars"]
    out.append(f"| {name} | {pct(tot['P2']['F'], F0)} | {pct(tot['P2']['dollars'], D0)} | {bF} ({pct(tot[bF]['F'], F0)}) | "
               f"{bD} ({pct(tot[bD]['dollars'], D0)}) | {'yes' if rF != rD else 'no'}"
               + (f" (orders differ at: {', '.join(p for p, q in zip(rF, rD) if p != q)})" if rF != rD else "") + " |")

if cuts:
    ratios = [f(c, "ratio_200") for c in cuts]
    clm_F = sum(f(r, "P1|F") for r in runs if r["source"] == "bcp-clm")
    out += ["", "## The 68 cut replies (CLM, BrowseComp-Plus)", "",
            f"- discarded thinking: {sum(f(c, 'G') for c in cuts):,.0f} generated tokens ({sum(f(c, 'thinking_tokens') for c in cuts):,.0f} "
            f"thinking tokens by our tokenizer); generation FLOPs {pct(sum(f(c, 'F_gen') for c in cuts), clm_F)} of CLM's executed FLOPs;",
            f"- the next reply also cut: {sum(c['next_cut'] == 'True' for c in cuts)} of {len(cuts)};",
            f"- similarity of the first 200 thinking tokens with the next reply's (difflib ratio): median {statistics.median(ratios):.2f}; "
            f">= 0.9: {sum(x >= 0.9 for x in ratios)}, >= 0.8: {sum(x >= 0.8 for x in ratios)}, >= 0.5: {sum(x >= 0.5 for x in ratios)}, "
            f"< 0.3: {sum(x < 0.3 for x in ratios)}; identical first 200 tokens: {sum(f(c, 'common_prefix') >= 200 for c in cuts)}; "
            f"median common prefix {statistics.median(f(c, 'common_prefix') for c in cuts):.0f} tokens;"]
    base = [f(c, "ratio_200_random") for c in cuts if c["ratio_200_random"] != ""]
    out.append(f"- baseline, a random reply of the same run more than 2 calls away: median {statistics.median(base):.2f}; "
               f">= 0.8: {sum(x >= 0.8 for x in base)}, >= 0.5: {sum(x >= 0.5 for x in base)}; replies whose next reply beats "
               f"the baseline by 0.2+: {sum(f(c, 'ratio_200') - f(c, 'ratio_200_random') >= 0.2 for c in cuts if c['ratio_200_random'] != '')};")
    for th in (0.8, 0.5):
        sim = [c for c in cuts if f(c, "ratio_200") >= th]
        out.append(f"- upper bound for \"resume, don't retry\" (the cut thinking of replies whose next reply restarts similarly, "
                   f"ratio >= {th}): {len(sim)} replies, {sum(f(c, 'G') for c in sim):,.0f} tokens, "
                   f"{pct(sum(f(c, 'F_gen') for c in sim), clm_F)} of CLM's executed FLOPs.")
# check: P0 rebuilds the bare conversation; on our arms without management it should match what ran (P1)
bare = [r for r in runs if r["arm"] in ("raw", "raw-32k", "base-32k", "base")]
if bare:
    out += ["", f"Check: on our arms without context management ({len(bare)} traces), P0 is "
            f"{pct(sum(f(r, 'P0|sent') for r in bare), sum(f(r, 'P1|sent') for r in bare))} of the tokens sent as executed and "
            f"{pct(sum(f(r, 'P0|F') for r in bare), sum(f(r, 'P1|F') for r in bare))} of the FLOPs (P0 leaves out harness nudges and "
            f"approximates message wrappers)."]
(D / "summary.md").write_text("\n".join(out) + "\n")
print("\n".join(out))
