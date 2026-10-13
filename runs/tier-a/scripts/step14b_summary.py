"""Step 14b: the report's tables, from runs/tier-a/step14b/{runs,items,stale,edits}.csv (step14b_rereads.py).
FIXED-TRAJECTORY ESTIMATES on the canonical track. Writes runs/tier-a/step14b/summary.md.
Use: .venv/bin/python runs/tier-a/scripts/step14b_summary.py"""

import csv
from collections import Counter, defaultdict
from pathlib import Path

D = Path("runs/tier-a/step14b")
runs = list(csv.DictReader((D / "runs.csv").open()))
edits = list(csv.DictReader((D / "edits.csv").open())) if (D / "edits.csv").exists() else []
stale = list(csv.DictReader((D / "stale.csv").open())) if (D / "stale.csv").exists() else []
f = lambda r, k: float(r[k] or 0)   # noqa: E731
pct = lambda a, z: f"{100 * a / z:.2f}%" if z else "–"   # noqa: E731
NO_EDITS = ("raw", "raw-32k", "base-32k", "base")
out = ["# Step 14b tables (fixed-trajectory estimates, canonical track)", ""]


def concentration(rs, key_from, key_to):
    saves = sorted((f(r, key_from) - f(r, key_to) for r in rs), reverse=True)
    total = sum(s for s in saves if s > 0)
    if total <= 0:
        return "–"
    acc, n = 0.0, 0
    for s in saves:
        acc += max(s, 0)
        n += 1
        if acc >= 0.8 * total:
            break
    return f"{n} of {len(rs)} runs give 80%"


def block(title, rs):
    if not rs:
        return
    F = sum(f(r, "F") for r in rs)
    mass = sum(f(r, "big_mass") for r in rs)
    E, V, P = (sum(f(r, "F") - f(r, "F_level1") for r in rs), sum(f(r, "F_level1") - f(r, "F_level2") for r in rs),
               sum(f(r, "F_level2") - f(r, "F_level3") for r in rs))
    newF = sum(f(r, "F_new_prefill") for r in rs)
    calls = sum(f(r, "agent_calls") for r in rs)
    out.append(f"\n### {title}: {len(rs)} runs, {F / 1e15:.1f} PFLOPs\n")
    out.append("| class | share of tool-output tokens (200+) | share with a visible earlier copy | PFLOPs saved | share of total FLOPs | share of new-prefill FLOPs | concentration |")
    out.append("|---|---|---|---|---|---|---|")
    e_tok = sum(f(r, "tok_E") + f(r, "unit_E-incidental") + f(r, "unit_E-deliberate") for r in rs)
    out.append(f"| E (references) | {pct(e_tok, mass)} | {pct(sum(f(r, 'visible_E') for r in rs), e_tok)} | "
               f"{E / 1e15:.3f} | **{pct(E, F)}** | {pct(E, newF)} | {concentration(rs, 'F', 'F_level1')} |")
    out.append(f"| V (diffs, beyond E) | {pct(sum(f(r, 'tok_V') for r in rs), mass)} | {pct(sum(f(r, 'visible_V') for r in rs), sum(f(r, 'tok_V') for r in rs))} | "
               f"{V / 1e15:.3f} | {pct(V, F)} | {pct(V, newF)} | {concentration(rs, 'F_level1', 'F_level2')} |")
    out.append(f"| P (chunk pointers, beyond E and V) | {pct(sum(f(r, 'tok_P') for r in rs), mass)} (shared spans {pct(sum(f(r, 'p_shared_tokens') for r in rs), mass)}) | "
               f"{pct(sum(f(r, 'visible_P') for r in rs), sum(f(r, 'p_shared_tokens') for r in rs))} of shared spans | {P / 1e15:.3f} | {pct(P, F)} | {pct(P, newF)} | {concentration(rs, 'F_level2', 'F_level3')} |")
    out.append(f"| N (not savable) | {pct(sum(f(r, 'tok_N') for r in rs), mass)} | – | – | – | – | – |")
    out.append(f"| R? (not savable) | {pct(sum(f(r, 'tok_R?') for r in rs), mass)} | – | – | – | – | – |")
    out.append(f"| U | {pct(sum(f(r, 'tok_U') for r in rs), mass)} | – | – | – | – | – |")
    nref = sum(f(r, "N_ref_cost") for r in rs)
    out.append(f"\nTool-output tokens in items of 200+: {mass:,.0f}; under 200: {sum(f(r, 'small_mass') for r in rs):,.0f}. "
               f"N: {sum(f(r, 'tok_N') for r in rs):,.0f} tokens; a reference plus its changed fields would cost {nref:,.0f}. "
               f"References {sum(f(r, 'references') for r in rs):,.0f}, diffs {sum(f(r, 'diffs') for r in rs):,.0f}, "
               f"chunk pointers {sum(f(r, 'pointers') for r in rs):,.0f}; repairs {sum(f(r, 'repairs') for r in rs):,.0f} "
               f"({sum(f(r, 'repaired_tokens') for r in rs):,.0f} tokens); unsupported "
               f"{pct(sum(f(r, 'unsupported_tokens') for r in rs), sum(f(r, 'candidate_tokens') for r in rs))} of candidate tokens. "
               f"Potentially stale current-state evidence: {pct(sum(f(r, 'dangerous_calls') for r in rs), calls)} of agent calls "
               f"(a V-class source, only an older version visible, the call's action touches it); calls with any such stale copy "
               f"visible: {pct(sum(f(r, 'stale_calls') for r in rs), calls)} ({sum(f(r, 'stale_tokens') for r in rs):,.0f} tokens). "
               f"Broader context (any file whose visible read predates a later write to it, V class or not): "
               f"{pct(sum(f(r, 'stale_broad_calls') for r in rs), calls)} of calls; with the action touching it "
               f"{pct(sum(f(r, 'dangerous_broad_calls') for r in rs), calls)}.")
    if any(r["bench"] == "bcp" for r in rs):
        out.append(f"E references here are snippet-level (repeated documents inside new search results). "
                   f"BrowseComp-Plus units (snippets and opened documents): E incidental "
                   f"{sum(f(r, 'unit_E-incidental') for r in rs):,.0f} tokens, E deliberate {sum(f(r, 'unit_E-deliberate') for r in rs):,.0f}, "
                   f"new {sum(f(r, 'unit_new') for r in rs):,.0f}.")


for bench, name in (("tblite", "TBLite"), ("bcp", "BrowseComp-Plus pilot")):
    rs = [r for r in runs if r["bench"] == bench]
    if not rs:
        continue
    F = sum(f(r, "F") for r in rs)
    clm = [r for r in rs if r["arm"].startswith("clm")]
    Fclm = sum(f(r, "F") for r in clm)
    E = sum(f(r, "F") - f(r, "F_level1") for r in rs)
    beyond = sum(f(r, "F_level1") - f(r, "F_level3") for r in rs)
    lost = sum(f(r, "F_lost") for r in clm)
    out += [f"\n## {name}", "",
            f"**(1) E savings, after repairs: {pct(E, F)} of total FLOPs. (2) Beyond E (V diffs + P chunks): {pct(beyond, F)}. "
            f"(3) CLM's plausibly avoidable management cost (retained text whose cache reuse its edits lost): {pct(lost, Fclm)} of "
            f"CLM's FLOPs (an upper bound).**", ""]
    block("Runs without edits (our harness: raw, raw-32k, base-32k, base)", [r for r in rs if r["arm"] in NO_EDITS])
    block("Runs with edits or management (econo+jev, summary, CLM)", [r for r in rs if r["arm"] not in NO_EDITS])
    for arm in sorted({r["arm"] for r in rs}):
        block(f"arm {arm}", [r for r in rs if r["arm"] == arm])
    if clm:
        out.append(f"\n### CLM's management cost ({name}; an upper bound)\n")
        calls = sum(f(r, k) for r in clm for k in ("mgmt_calls", "task_calls", "mixed_calls", "none_calls"))
        out.append(f"Calls: management-only {pct(sum(f(r, 'mgmt_calls') for r in clm), calls)}, mixed {pct(sum(f(r, 'mixed_calls') for r in clm), calls)}, "
                   f"task-only {pct(sum(f(r, 'task_calls') for r in clm), calls)}, no command {pct(sum(f(r, 'none_calls') for r in clm), calls)}. "
                   f"Generation on management (management-only calls, and the management commands' own text in mixed calls): "
                   f"{pct(sum(f(r, 'F_gen_mgmt') for r in clm), Fclm)} of CLM's FLOPs. Edits: {sum(f(r, 'edits') for r in clm):,.0f}.")
        es = [e for e in edits if any(e["run_id"] == r["run_id"] for r in clm)]
        out.append("\n| edit type | edits | new text tokens | re-prefill: new summary text | reasoning made visible | new observations | "
                   "retained text, reuse lost (avoidable) | lost-reuse PFLOPs | share of CLM's FLOPs |")
        out.append("|---|---|---|---|---|---|---|---|---|")
        for etype in ("summarize", "shorten", "drop", "reorder", "note", "rollback (budget control)", "other"):
            xs = [e for e in es if e["type"] == etype]
            if xs:
                out.append(f"| {etype} | {len(xs)} | {sum(f(e, 'new_tokens') for e in xs):,.0f} | {sum(f(e, 'summary') for e in xs):,.0f} | "
                           f"{sum(f(e, 'reasoning_made_visible') for e in xs):,.0f} | {sum(f(e, 'observations') for e in xs):,.0f} | "
                           f"{sum(f(e, 'retained') for e in xs):,.0f} | {sum(f(e, 'F_lost') for e in xs) / 1e15:.3f} | "
                           f"{pct(sum(f(e, 'F_lost') for e in xs), Fclm)} |")
        out.append(f"\nEdits whose request could not be mapped message by message: {sum(1 for e in es if e['mapped'] != 'True')} of {len(es)}.")
(D / "summary.md").write_text("\n".join(out) + "\n")
print("\n".join(out))
