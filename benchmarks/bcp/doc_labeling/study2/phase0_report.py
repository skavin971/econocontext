"""Write the Phase 0 results page from the private Study 1-2 data (aggregates only: no BCP text, answers or traces).

Inputs (under ~/econo/bcp): the trial folders (trials.load), judge verdicts (judge-phase0/<route>/), the trace audit
(phase0/trace_audit.json, from trace_audit), the headroom table (phase0/headroom.json, from headroom), and the
saved pages (order_audit). Output: docs/results/2026-10-27-phase0/README.md (or the path given).

Use: .venv/bin/python -m benchmarks.bcp.doc_labeling.study2.phase0_report [--out PATH]
"""

import argparse
import collections
import itertools
import json
import statistics
from pathlib import Path

from benchmarks.bcp.doc_labeling.common import Tokens, settings

from . import cost as C
from . import headroom as H
from . import order_audit as OA
from . import trials as T

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "docs" / "results" / "2026-10-27-phase0" / "README.md"
S2_ORDER = ["M0", "M1", "M2", "M3", "M4", "M5", "C1", "C2"]
S1_ORDER = ["M0", "S1-original", "S1-ranked", "M1", "S1-ranked_doc_labels"]


def verdicts(route, bcp=T.BCP):
    out = {}
    for f in (Path(bcp) / "judge-phase0" / route).glob("*/*.json"):
        v = json.loads(f.read_text())
        out[(v["study"], v["run"], v["qid"], v["arm"])] = v
    return out


def judged(t, vs):
    """True / False / None (unresolved) under the judge; abstentions and no-answer trials are False."""
    if not (t["answered"] and t["response"]):
        return False
    v = vs.get((t["study"], t["run"], t["qid"], t["arm"]))
    return None if v is None or v.get("parse_error") else bool(v["correct"])


def n(v, fmt="{:,.0f}"):
    return "–" if v is None else fmt.format(v)


def med(values, fmt="{:,.0f}"):
    v = [x for x in values if x is not None]
    return f"{fmt.format(statistics.median(v))} ({fmt.format(min(v))}–{fmt.format(max(v))})" if v else "–"


def accuracy_section(ts, vs, order, qids, route_label):
    lines = [f"| ID | " + " | ".join(f"Q{q}" for q in qids) + " | **Total judge** | contains-gold | exact | unresolved |",
             "|---|" + "---|" * (len(qids) + 4)]
    for arm in order:
        rows = [t for t in ts if t["arm_id"] == arm]
        if not rows:
            continue
        cells = []
        for q in qids:
            r = [t for t in rows if t["qid"] == q]
            j = [judged(t, vs) for t in r]
            cells.append(f"{sum(x is True for x in j)} / {sum(t['contains'] for t in r)} / {sum(t['exact'] for t in r)} of {len(r)}"
                         if r else "–")
        j = [judged(t, vs) for t in rows]
        lines.append(f"| {arm} | " + " | ".join(cells) + f" | **{sum(x is True for x in j)}/{len(rows)}** | "
                     f"{sum(t['contains'] for t in rows)}/{len(rows)} | {sum(t['exact'] for t in rows)}/{len(rows)} | "
                     f"{sum(x is None for x in j)} |")
    agree = [(judged(t, vs), t["contains"]) for t in ts if t["answered"] and judged(t, vs) is not None]
    both = sum(a == b for a, b in agree)
    lines += ["", f"Cells: judge ✓ / contains-gold ✓ / exact ✓ of n trials. Judge: {route_label}. Judge and "
              f"contains-gold agree on {both} of {len(agree)} answered trials "
              f"(judge ✓ contains ✗: {sum(a and not b for a, b in agree)}; judge ✗ contains ✓: "
              f"{sum(b and not a for a, b in agree)})."]
    return "\n".join(lines)


def cost_section(rows, ts):
    lines = ["| ID | trials | Jev requests (median) | Jev $ per trial, median (range) | Jev $ total | main P | main U (new) | "
             "main G (output) | main PFLOPs |", "|---|---|---|---|---|---|---|---|---|"]
    for arm in S2_ORDER:
        r = [x for x in rows if x["arm_id"] == arm and x["study"] == "s2"]
        if not r:
            continue
        lines.append(f"| {arm} | {len(r)} | {n(statistics.median(x['jev_requests'] for x in r), '{:.1f}')} | "
                     f"{med([x['jev_usd'] for x in r], '${:.4f}')} | ${sum(x['jev_usd'] for x in r):.4f} | "
                     f"{med([x['P'] for x in r])} | {med([x['U'] for x in r])} | {med([x['G'] for x in r])} | "
                     f"{med([x['PF'] for x in r], '{:.2f}')} |")
    s2 = [x for x in rows if x["study"] == "s2"]
    total = sum(x["jev_usd"] for x in s2)
    lines += ["", f"**Study 2 Jev total: ${total:.4f}** ({sum(x['jev_input_tokens'] for x in s2):,} billed input tokens, "
              f"{sum(x['jev_requests'] for x in s2)} requests; {sum(x['jev_failed_no_usage'] for x in s2)} failed without "
              f"usage, {sum(x['jev_retries'] for x in s2)} retries, {sum(x['jev_cache_hits'] for x in s2)} harness cache "
              f"hits). Study 1: ${sum(x['jev_usd'] for x in rows if x['study'] == 's1'):.4f}. Response model field: "
              "not logged (Jev.ask keeps answers and usage only); jev-latest = jev-1.14.0 throughout.", ""]
    be = C.break_even(s2)
    lines += ["**Break-even** (Jev $ ÷ main PFLOPs saved vs the paired M0 trial = the $/PFLOP above which Jev pays for "
              "itself):", "", "| ID | pairs | saved FLOPs | median $/PFLOP (range) | no saving |", "|---|---|---|---|---|"]
    for arm in S2_ORDER[1:6]:
        b = [x for x in be if x["arm_id"] == arm]
        if b:
            lines.append(f"| {arm} | {len(b)} | {sum(x['usd_per_pflop'] is not None for x in b)} | "
                         f"{med([x['usd_per_pflop'] for x in b], '${:.5f}')} | {sum(x['usd_per_pflop'] is None for x in b)} |")
    splits = [s for x in s2 for s in x["splits"]]
    lines += ["", "**Where SI and SR request characters go** (median share of each request's characters):", "",
              "| Method | Unit | requests | state (chunks, topics, question) | repeated per-question text |",
              "|---|---|---|---|---|"]
    for kind in ("SI", "SR"):
        for unit in ("search", "document"):
            v = [s for s in splits if s["kind"] == kind and s["unit"] == unit]
            if v:
                lines.append(f"| {kind} | {unit} | {len(v)} | {statistics.median(s['state_share'] for s in v):.0%} | "
                             f"{statistics.median(s['repeated_share'] for s in v):.0%} |")
    return "\n".join(lines), total


def audit_section(audit):
    lines = ["**Repeats within a trial** (pooled per arm; uniform Qwen3-0.6B chunks, 102 snippet / 400 document tokens):", "",
             "| ID | trials | documents opened | re-opens | chunks returned | repeated returns | chunks admitted | "
             "repeated admissions |", "|---|---|---|---|---|---|---|---|"]
    by = collections.defaultdict(list)
    for t in audit:
        by[t["arm_id"]].append(t)
    for arm in S2_ORDER:
        r = by.get(arm, [])
        if not r:
            continue
        s = collections.Counter()
        for t in r:
            s.update(t["repeats"])
        lines.append(f"| {arm} | {len(r)} | {s['documents_opened']} | {s['document_reopens']} | {s['chunks_returned']:,} | "
                     f"{s['chunks_returned_repeats']:,} | {s['chunks_admitted']:,} | {s['chunks_admitted_repeats']:,} |")
    lines += ["", "**Cut replies** (output cap hit, no valid action):", "",
              "| ID | trials with one | cut replies | their completion tokens | share of main FLOPs |", "|---|---|---|---|---|"]
    for arm in S2_ORDER:
        r = [t for t in by.get(arm, []) if t["cut"]]
        if not r:
            continue
        cut = sum(t["cut"]["cut_replies"] for t in r)
        F = sum(t["cut"]["total_F"] for t in r)
        lines.append(f"| {arm} | {sum(t['cut']['cut_replies'] > 0 for t in r)}/{len(r)} | {cut} | "
                     f"{sum(t['cut']['cut_completion_tokens'] for t in r):,} | "
                     f"{(sum(t['cut']['cut_F'] for t in r) / F if F else 0):.1%} |")
    lines += ["", "**Recognition gap** (incorrect trials under contains-gold; evaluation only):", "",
              "| ID | incorrect | search returned a gold doc | a gold chunk on the page read | gold doc opened |",
              "|---|---|---|---|---|"]
    for arm in S2_ORDER:
        r = [t for t in by.get(arm, []) if not t["contains"]]
        if r:
            lines.append(f"| {arm} | {len(r)} | {sum(t['recognition']['gold_returned'] for t in r)} | "
                         f"{sum(t['recognition']['gold_on_page1'] for t in r)} | {sum(t['recognition']['gold_opened'] for t in r)} |")
    return "\n".join(lines)


def order_section(ts, recs):
    tokens = Tokens(settings({"repeats": 1}))
    audits = collections.defaultdict(list)
    for t in ts:
        if t["family"] == "ours" and t["arm"] in OA.DOC_ARMS:
            audits["SI (M2, M3)" if t["arm"] in ("ranked_doc_search", "ranked_doc_search_labels") else
                   ("SR (M4, M5)" if t["arm"].startswith("strict") else "M1")].append(
                OA.audit_trial(t, recs[t["qid"]], tokens.count))
    lines = ["| Method | paged searches | Jev p1 ∩ hit-order p1 (median) | Jev p1 ∩ lead-first p1 (median) | gold/evidence "
             "doc × search | any chunk on p1: Jev / hit / lead | answer-string searches: on p1 Jev / hit / lead | opened "
             "documents: overlap with original p1 (median) | answer chunk on p1: Jev / original |",
             "|---|---|---|---|---|---|---|---|---|"]
    for k in ("M1", "SI (M2, M3)", "SR (M4, M5)"):
        s = OA.summarize(audits[k])
        f = lambda d: f"{d['median']:.0%}" if d else "–"
        lines.append(f"| {k} | {s['searches']} | {f(s['overlap_hit'])} | {f(s['overlap_lead'])} | {s['relevant']} | "
                     f"{s['any']['jev']} / {s['any']['hit']} / {s['any']['lead']} | {s['answer']['jev']} / "
                     f"{s['answer']['hit']} / {s['answer']['lead']} of {s['answer_searches']} | {f(s['overlap_orig'])} "
                     f"({s['documents']} views) | {s['doc_answer']['jev']} / {s['doc_answer']['orig']} of {s['doc_answer_n']} |")
    return "\n".join(lines)


def headroom_section(h):
    order = ["snippet", "page1", "deep", "absent"]
    q = collections.Counter(r["earliest"] for r in h)
    d = collections.Counter(x["bucket"] for r in h for x in r["docs"])
    p = collections.Counter(r["earliest"] for r in h if r["pilot"])
    lines = ["| Bucket (first answer-string occurrence, Qwen3-0.6B tokens) | dev questions (earliest over gold docs) | "
             "of which pilot | gold documents |", "|---|---|---|---|"]
    names = {"snippet": "≤ 512 (inside the snippet)", "page1": "513–2,000 (page 1, original order)",
             "deep": "2,001–8,192 (needs paging or ranking)", "absent": "not in the 8,192-token view"}
    for b in order:
        lines.append(f"| {names[b]} | {q[b]} | {p[b]} | {d[b]} |")
    lines.append(f"| **total** | **{len(h)}** | **{sum(p.values())}** | **{sum(d.values())}** |")
    return "\n".join(lines)


def phase1_section(ts, vs, rows, use_judge):
    grade = (lambda t: judged(t, vs)) if use_judge else (lambda t: t["contains"])
    ids = [a for a in S2_ORDER if any(t["arm_id"] == a for t in ts)]
    by = collections.defaultdict(list)
    for t in ts:
        by[(t["arm_id"], t["qid"])].append(grade(t))
    lines = ["**How often two arms disagree on the same question** (share of all trial pairs, both repeats of each, "
             "pooled over the 5 questions; unresolved verdicts skipped):", "",
             "| | " + " | ".join(ids) + " |", "|---|" + "---|" * len(ids)]
    qids = sorted({t["qid"] for t in ts})
    for a in ids:
        cells = []
        for b in ids:
            pairs = [(x, y) for q in qids for x in by[(a, q)] for y in by[(b, q)] if x is not None and y is not None]
            cells.append(f"{sum(x != y for x, y in pairs) / len(pairs):.2f}" if pairs and a != b else "–")
        lines.append(f"| **{a}** | " + " | ".join(cells) + " |")
    lines += ["", "**Variation across the 3 repeats** (per arm and question: max ÷ min; then the median over questions):", "",
              "| ID | PFLOPs max/min | Jev $ max/min | wall time per trial, median (range), s |", "|---|---|---|---|"]
    for a in ids:
        pf, usd = [], []
        for q in qids:
            r = [x for x in rows if x["arm_id"] == a and x["qid"] == q and x["study"] == "s2"]
            p = [x["PF"] for x in r if x["PF"]]
            j = [x["jev_usd"] for x in r if x["jev_usd"]]
            if len(p) > 1:
                pf.append(max(p) / min(p))
            if len(j) > 1:
                usd.append(max(j) / min(j))
        wall = [x["wall_s"] for x in rows if x["arm_id"] == a and x["study"] == "s2"]
        lines.append(f"| {a} | {n(statistics.median(pf), '{:.2f}') if pf else '–'} | "
                     f"{n(statistics.median(usd), '{:.2f}') if usd else '–'} | {med(wall)} |")
    dev, pilot = H.dev_ids()
    rest = [q for q in dev if q not in set(pilot)]
    lines += ["", f"**The {len(rest)} dev questions not in the pilot set** (benchmarks/bcp/split.json): "
              + ", ".join(rest) + "."]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    bcp = T.BCP
    recs = T.records(bcp)
    s2, s1 = T.load("s2", bcp), T.load("s1", bcp)
    official = verdicts("vllm", bcp)
    vs, label = (official, "official (vLLM Qwen/Qwen3-32B)") if official else (
        verdicts("purdue", bcp), "**provisional**: Purdue qwen3:32b, an Ollama build (quantization unknown, likely "
        "4-bit), upstream prompt and sampling with thinking off")
    rows = [C.trial_cost(t, recs[t["qid"]]) for t in s2 + s1]
    audit = json.loads((bcp / "phase0" / "trace_audit.json").read_text())
    head = json.loads((bcp / "phase0" / "headroom.json").read_text())
    template = Path(__file__).with_name("phase0_template.md").read_text()
    cost_md, total = cost_section(rows, s2)
    page = (template
            .replace("{{JUDGE}}", label)
            .replace("{{ACC_S2}}", accuracy_section(s2, vs, S2_ORDER, list(T.STUDY2_QIDS), label))
            .replace("{{ACC_S1}}", accuracy_section(s1, vs, S1_ORDER, list(T.STUDY1_QIDS), label))
            .replace("{{COST}}", cost_md)
            .replace("{{AUDIT}}", audit_section(audit))
            .replace("{{ORDER}}", order_section(s2, recs))
            .replace("{{HEADROOM}}", headroom_section(head))
            .replace("{{PHASE1}}", phase1_section(s2, vs, rows, use_judge=bool(vs))))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(page)
    print(f"wrote {a.out}; Study 2 Jev total ${total:.4f}")


if __name__ == "__main__":
    main()
