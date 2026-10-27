"""Phase 0, T3: what each trial cost (read-only over the private trial folders).

Jev, in dollars (never FLOPs):
- price: $0.042 per million *input* tokens; output is free; one rate for state and question tokens; no caching
  discount (TypeSafe list price, docs.typesafe.ai/models.md, checked 2026-10-26).
- tokens: Jev's own reported usage.input_tokens ("billable input tokens") on each logged request; never a character
  estimate.
- every request row counts, retries included; a failed request that returned no usage is listed separately (it may
  still have been billed; we cannot know its tokens).
- harness cache hits (a search or document unit already scored in this trial, so no request was sent) cost $0 and
  are counted.
- the response's model field: Jev.ask (econocontext/predictor/jev.py) keeps only answers and usage, so it was never
  logged; recorded as None. jev-latest pointed to jev-1.14.0 for the whole of Study 1-2 (since 2026-10-14).
- per request, the character split of the request body: state (topics, chunk texts, question, reasoning, JSON) vs
  questions, and the share taken by the per-question instruction text that repeats once per (chunk, topic).
  Tokens are apportioned by those character shares of the request's billed input tokens.

Main model: prefix-reuse FLOPs (CLM Eq. 9, measure/flops.py) unchanged, plus per-trial total prompt tokens (P),
new non-reused prompt tokens (U = P - R, R from the 16-token-block cache simulation) and output tokens (G). No
dollar price on the main model.

Break-even, per ranked arm vs the M0 trial of the same run folder (same question and repeat): Jev $ divided by the
main PFLOPs saved = the $/PFLOP at which Jev pays for itself. A trial that saved no FLOPs has no break-even.
"""

import collections
import json
import statistics
from pathlib import Path

from benchmarks.bcp.doc_labeling import search_pilot as sp
from benchmarks.bcp.doc_labeling.common import digest

from . import order_audit as oa
from . import trials as T

PRICE_PER_INPUT_TOKEN = 0.042 / 1_000_000
JEV_ARMS = ("ranked", "ranked_doc", "ranked_doc_labels") + sp.SEARCH_ARMS


def jev_rows(trial_dir):
    """(unit key, row) for every logged Jev request of one trial, in file order."""
    for f in sorted(Path(trial_dir).glob("**/jev.jsonl")):
        key = f.parent.relative_to(trial_dir).as_posix()
        for line in f.read_text().splitlines():
            if line.strip():
                yield key, json.loads(line)


def tool_rows(trial_dir):
    return T.rows(Path(trial_dir) / "tools.jsonl")


def unit_inputs(trial, record):
    """unit key -> (state, questions per request) rebuilt from the saved files, for the character split.
    SI units keep their chunks and topics in rankings.json; SR units keep chunk text in pages.json and their topics
    in the tool call that made them (matched by the unit folder's digest). M5's reasoning text is not saved per
    unit; its sent length is (reasoning_chars.sent), so its state size is that length plus JSON quoting."""
    arm, trial_dir = trial["arm"], Path(trial["trial_dir"])
    with_question = arm in ("ranked_doc", "ranked_doc_search")
    topics_by_key = {}
    for row in tool_rows(trial_dir):
        args = row.get("arguments") or {}
        if not args.get("topics"):
            continue
        try:
            topics = sp.requested_topics(args["topics"])
        except ValueError:
            continue
        if row.get("tool") == "search" and isinstance(row.get("result"), dict):
            key = "search/" + digest({"query": args["query"], "topics": topics, "hits": row["result"]["hits"]})[:12]
        elif row.get("tool") == "get_document":
            key = f"documents/{args.get('docid')}/{digest(topics)[:12]}"
        else:
            continue
        topics_by_key[key] = topics
    out = {}
    for f in sorted(trial_dir.glob("**/rankings.json")):
        key = f.parent.relative_to(trial_dir).as_posix()
        rk = json.loads(f.read_text())
        if "rankings" in rk:  # Score + Interleave
            topics = [r["topic"] for r in rk["rankings"]]
            chunks = sorted(rk["rankings"][0]["chunks"], key=lambda c: c["ordinal"])
            state = {**({"question": record["query"]} if with_question else {}), "topics": topics,
                     "document": [{"chunk_id": c["chunk_id"], "text": c["text"]} for c in chunks]}
            prompt = sp.DOC_SCORE_PROMPT if with_question else sp.DOC_LABEL_PROMPT
            questions = {f"{c['chunk_id']}_{t['id']}": {"type": "noul", "instructions": prompt.format(
                chunk_id=c["chunk_id"], **t)} for c in chunks for t in topics}
            out[key] = {"kind": "SI", "state": state, "questions": questions}
        elif "order" in rk and key in topics_by_key:  # Strict Rank
            topics = topics_by_key[key]
            chunks = sorted(oa.parse_pages(json.loads((f.parent / "pages.json").read_text())).values(),
                            key=lambda c: c["ordinal"])
            state = {"topics": topics, "document": [{"chunk_id": c["chunk_id"], "text": c["text"]} for c in chunks]}
            names = "; ".join(t["description"] for t in topics)
            reasoning = arm == "strict_rank_search_reasoning"
            questions = {"rank": {"type": "choice", "instructions": (sp.SR_REASONING_PROMPT if reasoning else sp.SR_PROMPT)
                                  .format(topics=names), "criteria": {c["chunk_id"]: f"chunk {c['chunk_id']} in state.document"
                                                                      for c in chunks}},
                         "exists": {"type": "noul", "instructions": sp.SR_EXISTS_PROMPT.format(topics=names)}}
            out[key] = {"kind": "SR", "state": state, "questions": questions, "reasoning": reasoning}
    return out


def split_chars(unit, row):
    """Character split of one request: state, questions, and the repeated-instruction part of the questions."""
    state_chars = len(json.dumps(unit["state"]))
    if unit["kind"] == "SR" and unit.get("reasoning"):
        sent = ((row.get("reasoning_chars") or {}).get("sent")) or 0
        state_chars += len(json.dumps({"agent_reasoning": ""})) - 2 + sent + 2  # key, quotes, text (approximate)
    keys = row.get("question_keys") or list(unit["questions"])
    asked = {k: unit["questions"][k] for k in keys if k in unit["questions"]}
    question_chars = len(json.dumps(asked))
    instruction_chars = sum(len(q["instructions"]) for q in asked.values())
    if unit["kind"] == "SR":  # the per-chunk part that repeats is the criteria labels, not the one instruction
        instruction_chars = sum(len(v) for q in asked.values() for v in (q.get("criteria") or {}).values())
    total = state_chars + question_chars
    return {"state_chars": state_chars, "question_chars": question_chars, "repeated_chars": instruction_chars,
            "repeated_share": instruction_chars / total if total else None,
            "state_share": state_chars / total if total else None}


def trial_cost(trial, record):
    """Jev and main-model cost of one trial (aggregates; no text)."""
    s = trial["summary"]
    row = {"study": trial["study"], "qid": trial["qid"], "arm": trial["arm"], "arm_id": trial["arm_id"],
           "repeat": trial["repeat"], "run": trial["run"], "family": trial["family"]}
    if trial["family"] == "ours":
        fv = s.get("flops_validation") or {}
        row.update(P=fv.get("P"), U=fv.get("U"), G=fv.get("G"), PF=(s["model_flops"] / 1e15) if s.get("model_flops") else None,
                   wall_s=(s.get("elapsed_ms") or 0) / 1000 or None)
    else:
        m = clm_measure(trial)
        row.update(P=m.get("P"), U=m.get("U"), G=m.get("G"),
                   PF=s.get("PF") if not s.get("invalid") else None, wall_s=s.get("seconds"))
    if trial["arm"] not in JEV_ARMS or trial["family"] != "ours":
        row.update(jev_requests=0, jev_completed=0, jev_failed_no_usage=0, jev_retries=0, jev_cache_hits=0,
                   jev_input_tokens=0, jev_usd=0.0, jev_model=None, splits=[])
        return row
    units = unit_inputs(trial, record) if trial["arm"] != "ranked" else {}
    requests = completed = failed = 0
    tokens = 0
    batches = collections.Counter()
    splits = []
    for key, r in jev_rows(trial["trial_dir"]):
        requests += 1
        batches[(key, tuple(r.get("question_keys") or ()))] += 1
        usage = r.get("usage")
        if r.get("status") == "completed" and usage:
            completed += 1
            tokens += usage.get("input_tokens", 0)
            if key in units:
                split = split_chars(units[key], r)
                split.update(kind=units[key]["kind"], input_tokens=usage.get("input_tokens", 0),
                             unit="search" if key.startswith("search") else "document")
                splits.append(split)
        else:
            failed += 1
    retries = sum(n - 1 for n in batches.values())
    calls = collections.Counter(t.get("tool") for t in tool_rows(trial["trial_dir"]))
    scored_calls = (calls["search"] if trial["arm"] in sp.SEARCH_ARMS else 0) + calls["get_document"]
    unit_count = len({k for k, _ in batches})
    row.update(jev_requests=requests, jev_completed=completed, jev_failed_no_usage=failed, jev_retries=retries,
               jev_cache_hits=max(0, scored_calls - unit_count), jev_input_tokens=tokens,
               jev_usd=tokens * PRICE_PER_INPUT_TOKEN, jev_model=None, splits=splits)
    return row


_clm_cache = {}


def clm_measure(trial):
    """P, U, G of a CLM trial from measure/ (as benchmarks/bcp/run.py measured its FLOPs), cached privately."""
    log = trial.get("calls_log")
    if not log or not Path(log).exists():
        return {}
    cache = Path(T.BCP) / "cache" / "clm-measure" / f"{trial['arm']}.{trial['run']}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    from benchmarks.tblite.run import measure
    totals = measure({"calls_log": str(log)})
    keep = {k: totals.get(k) for k in ("P", "R", "U", "G", "F", "calls", "valid", "invalid")}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(keep))
    return keep


def break_even(rows):
    """Per ranked trial paired with M0 of the same run: Jev $ / main PFLOPs saved."""
    m0 = {(r["study"], r["run"], r["qid"]): r for r in rows if r["arm"] == "full"}
    out = []
    for r in rows:
        base = m0.get((r["study"], r["run"], r["qid"]))
        if r["arm"] not in JEV_ARMS or not base or r["PF"] is None or base["PF"] is None:
            continue
        saved = base["PF"] - r["PF"]
        out.append({**{k: r[k] for k in ("study", "qid", "arm", "arm_id", "repeat", "run")},
                    "jev_usd": r["jev_usd"], "pflops_saved": saved,
                    "usd_per_pflop": r["jev_usd"] / saved if saved > 0 else None})
    return out


def describe(values):
    v = [x for x in values if x is not None]
    return {"n": len(v), "median": statistics.median(v), "min": min(v), "max": max(v)} if v else {"n": 0}
