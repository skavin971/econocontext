"""PFLOP accuracy: check our PFLOPs math on one batch of CLM runs (pflop_accuracy/README.md).

For every run of the label (~/econo/bcp/runs/<label>/clm/*/), from the gateway's log of exactly what was sent and
returned (calls.jsonl) and CLM's own export (trajectory.ctx.json):
  1. Tokens: our rebuild of each prompt (measure/flops.py prompt_ids, with the tokenizer of the server that ran it)
     against the server's own prompt token ids.
  2. Cache hits (Path B only; Purdue reports none): the cached tokens our simulator predicts (measure/cache_sim.py, on
     the server's ids) against the cached tokens vLLM reports, call by call.
  3. PFLOPs (Eq. 9, measure/flops.py): ours on our rebuild ("ours"), ours on the server's ids, the same with the
     server's real cache hits ("real cache", Path B), and CLM's own number from its export ("CLM's own").
Prints a short verdict and writes runs/pflop-accuracy/<label>.md (numbers and question ids only).

Use:  .venv/bin/python pflop_accuracy/check_pflops.py --label bcp-pflop-vllm-20261020
"""

import argparse
import json
import sys
from pathlib import Path

from common import GATEWAY_LOGS, HF_MODEL, RESULTS, ROOT, RUNS

sys.path.insert(0, str(ROOT))
from measure.flops import measure_log  # noqa: E402
from measure.models import load  # noqa: E402
from measure.tokens import tokenizer_for_log  # noqa: E402


def clm_own(trial_dir: Path) -> dict:
    """CLM's own FLOPs export for the run (its harness writes it at the end of every trial)."""
    path = trial_dir / "agent" / "trajectory.ctx.json"
    if not path.exists():
        return {}
    k = ((json.loads(path.read_text()).get("final_metrics") or {}).get("extra") or {}).get("kv_cache_flops") or {}
    return {"F": k.get("cache_aware_flops"), "source": k.get("prefill_source"), "hits": k.get("cache_hit_tokens")}


def check_run(summary_path: Path, c_token: float, c_attn: float) -> dict:
    s = json.loads(summary_path.read_text())
    log = Path(s.get("calls_log") or "")
    if not log.is_absolute():
        log = ROOT / log
    if not log.exists():
        log = GATEWAY_LOGS / s["run_id"] / "calls.jsonl"
    m = measure_log(log, tokenizer_for_log(log, HF_MODEL), c_token, c_attn)
    t, checks = m["totals"], m["validation"]
    provider = next((json.loads(x).get("provider") for x in log.read_text().splitlines() if x.strip()), "purdue")
    return {"qid": s["task"], "provider": provider, "calls": t["calls"], "valid": t["valid"], "invalid": t["invalid"],
            "identical": sum(c["note"] == "identical" for c in checks),
            "reordered": sum("keys reordered" in c["note"] for c in checks),
            "split_only": t["split_only_calls"], "F": t["F"], "F_server": t["F_server"], "F_real": t.get("F_real"),
            "cache_equal": t.get("cache_equal"), "cache_max": t.get("cache_max_abs_diff"),
            "cache_abs": t.get("cache_abs_diff"), "R_sim": t.get("R_server_sim"), "R_real": t.get("R_real"),
            "first_diff": t.get("cache_first_diff"), "P": t["P"], "clm": clm_own(Path(s.get("trial_dir") or ""))}


def pf(x) -> str:
    return "–" if x is None else f"{x / 1e15:.3f}"


def main() -> None:
    ap = argparse.ArgumentParser(description="PFLOP accuracy: check our PFLOPs math on a batch of CLM runs")
    ap.add_argument("--label", required=True)
    a = ap.parse_args()
    summaries = sorted((RUNS / a.label / "clm").glob("*/summary.json"))
    if not summaries:
        raise SystemExit(f"no runs under {RUNS / a.label / 'clm'}: run pflop_accuracy/run_clm.py first")
    k = load(HF_MODEL)
    rows = [check_run(p, k["C_token"], k["C_attn"]) for p in summaries]
    has_real = all(r["F_real"] is not None for r in rows)
    calls = sum(r["calls"] for r in rows)
    out = [f"# PFLOP accuracy: {a.label}", "",
           f"CLM (zero-shot, as released) on {len(rows)} BrowseComp-Plus question(s), BM25 retriever; server: "
           f"{', '.join(sorted({r['provider'] for r in rows}))}. Prefix-reuse PFLOPs (Eq. 9), per question.", "",
           "| question | calls | ids valid | cache: sim = real (calls) | max cache diff (tokens) | PF ours | PF ours, server ids "
           "| PF real cache | PF CLM's own | CLM's prefill source |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        cache = f"{r['cache_equal']}/{r['calls']}" if r["F_real"] is not None else "– (not reported)"
        out.append(f"| {r['qid']} | {r['calls']} | {r['valid']}/{r['calls']} | {cache} | "
                   f"{r['cache_max'] if r['F_real'] is not None else '–'} | {pf(r['F'])} | {pf(r['F_server'])} | "
                   f"{pf(r['F_real'])} | {pf(r['clm'].get('F'))} | {r['clm'].get('source') or '–'} |")
    tot = lambda key: sum(r[key] or 0 for r in rows)   # noqa: E731
    clm_total = sum(r["clm"].get("F") or 0 for r in rows)
    out.append(f"| **all** | {calls} | {tot('valid')}/{calls} | "
               + (f"{tot('cache_equal')}/{calls}" if has_real else "–") + f" | "
               + (f"{max(r['cache_max'] for r in rows)}" if has_real else "–")
               + f" | {pf(tot('F'))} | {pf(tot('F_server'))} | {pf(tot('F_real') if has_real else None)} | "
               f"{pf(clm_total or None)} | |")
    verdict = ["", "## Verdict", ""]
    verdict.append(f"- **Tokens:** {tot('valid')} of {calls} calls validated against the server's ids "
                   f"({tot('identical')} identical, {tot('reordered')} with tool-schema keys reordered, "
                   f"{tot('split_only')} split-only). " + ("All match." if tot("invalid") == 0 else
                                                          f"**{tot('invalid')} do not: report them.**"))
    if has_real:
        share = tot("cache_equal") / calls if calls else 0
        first = next((r for r in rows if r["first_diff"]), None)
        verdict.append(f"- **Cache hits:** our simulator equals vLLM's cached tokens on {tot('cache_equal')} of {calls} calls "
                       f"({100 * share:.1f}%); total cached tokens ours {tot('R_sim'):,} vs vLLM {tot('R_real'):,}; "
                       f"mean difference {tot('cache_abs') / calls:.1f} tokens per call, largest {max(r['cache_max'] for r in rows)}."
                       + (f" First difference: question {first['qid']}, call {first['first_diff']['call_no']} "
                          f"(P {first['first_diff']['P']}, ours {first['first_diff']['R_sim']}, vLLM {first['first_diff']['R_real']})."
                          if first else ""))
        verdict.append(f"- **PFLOPs:** ours on the server's ids {pf(tot('F_server'))} vs with vLLM's real cache {pf(tot('F_real'))} "
                       f"(ratio {tot('F_server') / tot('F_real'):.4f}); ours on our rebuild {pf(tot('F'))}.")
    else:
        verdict.append("- **Cache hits:** not checkable on this server: it reports no cached tokens (Purdue never does). "
                       "Use Path B (our vLLM) for this check.")
        verdict.append(f"- **PFLOPs:** ours {pf(tot('F'))}; on the server's ids {pf(tot('F_server'))}.")
    if clm_total:
        verdict.append(f"- **Against CLM's own export:** ours on the server's ids / CLM's own = {tot('F_server') / clm_total:.4f} "
                       f"(CLM's prefill source: {', '.join(sorted({str(r['clm'].get('source')) for r in rows}))}).")
    out += verdict
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"{a.label}.md").write_text("\n".join(out) + "\n")
    print("\n".join(out))
    print(f"\nSaved: {RESULTS / f'{a.label}.md'} (numbers only; this is what to send back)")


if __name__ == "__main__":
    main()
