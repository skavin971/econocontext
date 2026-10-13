"""Step 13b: does the data support forecast-driven context? Offline, no model calls. A REPLAY of fixed
trajectories, not live behaviour.

Each run becomes an append-only stream, in the order its agent calls first show each message: the system
prompt and task, the model's replies (as resent, reasoning removed), the tool outputs as first shown, and the
harness's other user messages. Summary calls are left out. With no eviction this stream is the run with no
context management; for arms that had none (raw, raw-32k, base-32k) it must equal the logged prompts token for
token, which checks the rebuild.

Items: TBLite: every tool output of 1,000+ tokens; BrowseComp-Plus: every search result and opened document.
Needed again: a later model turn (reasoning, text or tool-call arguments) quotes the item (a 30-character
substring, of 5+ distinct characters, that no other message in the context at that moment contains) or names
something only the item contains at that moment: a path, a symbol (12c's rule), a document ID, or a name (2+
capitalized words). Turns later: the use's agent call minus the call that first showed the item, plus 1.

Replay under the limit (TBLite: 29,952 = 32,000 - 2,048; BrowseComp-Plus: 23,560 - 2,048 = 21,512), FORCED
eviction only (step 13b, as redone): only runs whose stream would put some call over the limit are included, and
eviction happens only when the next call would not fit, removing the minimum needed (see replay()):
  none (the stream as is, over the limit), stop (base's rule on the same trajectory: the run ends there),
  oldest, cheapest (least text after the item), knowing (only items never needed again, cheapest first).
A later need for an evicted item costs one extra call (median G) that re-shows it. If a call cannot be made to fit,
the run stops and does not reach its answer. ASSUMPTIONS: every logged call keeps its logged G; the re-shown item is
the same text. Every replayed prompt is rebuilt with measure/tokens.py's tokenizer (Purdue's split, step 13) and
priced with Eq. 9 and the cache simulator, on the canonical rebuild (step 14a), as are the logged runs (Purdue's
own ids give a variant only).
Compared with what base and summary actually did on the same tasks (TBLite: raw-32k, base-32k, summary-32k; the
BrowseComp-Plus pilot: base).

Use: PYTHONPATH=. .venv/bin/python runs/tier-a/scripts/step13b_forecast.py tblite [--workers 6]
Output: runs/tier-a/step13b/<benchmark>/{runs,items}.csv and summary.md
"""

import argparse
import csv
import json
import re
import statistics
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import step12b_analysis as b  # noqa: E402
import step12c_analysis as c  # noqa: E402

from measure.cache_sim import cached_prefix  # noqa: E402
from measure.flops import eq9, measure_log, prompt_ids  # noqa: E402
from measure.models import MEASURED, load  # noqa: E402
from measure.tokens import count_text, tokenizer  # noqa: E402

OUT = b.ROOT / "runs" / "tier-a" / "step13b"
LIMITS = {"tblite": 29_952, "bcp": 21_512}
POLICIES = ("none", "stop", "oldest", "cheapest", "knowing")
NAME = re.compile(r"\b[A-Z][\w'’.-]+(?:\s+[A-Z][\w'’.-]+)+")
DOCID = re.compile(r'"docid"\s*:\s*"?(\w+)"?')
NO_MANAGEMENT = {"raw", "raw-32k", "base-32k", "base"}


def count(text: str) -> int:
    return count_text(text, MEASURED)


def marker(m: dict) -> str:
    return "[EconoContext] This output was removed from the conversation; repeat the call to get it back."


def distinctive(text: str, bench: str) -> set[str]:
    out = b.paths(text) | {w for w in c.IDENT.findall(text) if len(w) >= 4 and (
        "_" in w or any(ch.isdigit() for ch in w) or any(ch.isupper() for ch in w[1:]) or len(w) >= 8)}
    if bench == "bcp":
        out |= set(DOCID.findall(text)) | set(NAME.findall(text))
    return out


def stream_of(rows: list[dict]) -> tuple[list[dict], list[int]]:
    """The append-only stream and, per agent call, how many stream messages its prompt holds."""
    stream, seen, upto = [], set(), []
    for row in rows:
        repeats = defaultdict(int)          # the same message text twice in one prompt (a repeated nudge) is two
        for m in row["request"]["messages"]:
            base = b.identity(m)
            key = (base, repeats[base])
            repeats[base] += 1
            if key not in seen:
                seen.add(key)
                stream.append({k: v for k, v in m.items() if k not in ("reasoning", "reasoning_content",
                                                                       "provider_specific_fields")})
        upto.append(len(stream))
    return stream, upto


def uses_of(stream, upto, replies, items, bench) -> dict[int, list[int]]:
    """For each item (a stream index), the agent calls whose reply needs it (see the module docstring)."""
    texts = [b.clean(" ".join(b.message_texts(m))) for m in stream]
    gram_owner, gram_count, word_owner, word_count = {}, defaultdict(int), {}, defaultdict(int)
    item_set = set(items)
    uses = defaultdict(list)
    added = 0
    for t, reply in enumerate(replies):
        while added < upto[t]:                      # the messages in call t's prompt
            for g in b.grams(texts[added]):
                gram_count[g] += 1
                gram_owner[g] = added
            for w in distinctive(texts[added], bench):
                word_count[w] += 1
                word_owner[w] = added
            added += 1
        said = "\n".join([reply.get("reasoning_content") or reply.get("reasoning") or "",
                          b.text_of(reply.get("content"))] + b.arg_strings(reply))
        hit = set()
        for i in range(len(said) - b.QUOTE + 1):
            g = said[i:i + b.QUOTE]
            if gram_count.get(g) == 1 and gram_owner[g] in item_set:
                hit.add(gram_owner[g])
        for w in distinctive(said, bench):
            if word_count.get(w) == 1 and word_owner[w] in item_set:
                hit.add(word_owner[w])
        for i in hit:
            uses[i].append(t)
    return uses


def replay(policy, stream, upto, items, item_tokens, uses, arrival, G, limit, opts, tok, median_G):
    """The prompts (token ids) and Gs of the run replayed under one policy (FORCED eviction, step 13b):
      none     the stream as is (calls may pass the limit);
      stop     base's rule on the same trajectory: the run ends at the first call over the limit;
      oldest   when the next call would pass the limit, evict the oldest items, the minimum needed to fit;
      cheapest the same, items with the least text after them first (nearest the end of the prompt);
      knowing  the same, only items never needed again from this call on, cheapest first.
    A later need for an evicted item costs one extra call (median G) that re-shows it there. If nothing more can
    be evicted and the call still does not fit, the run stops (it does not reach its answer).
    Returns (prompts, gens, refetches, stopped_at_call or None, evictions)."""
    present = {i: True for i in items}            # item -> shown in full
    restored: list[tuple[int, int]] = []          # (stream position it was re-shown at, item), in order
    prompts, gens, refetches, evictions = [], [], 0, 0
    marker_tokens = {i: count(marker(stream[i])) for i in items}

    def build(t):
        msgs = []
        for k in range(upto[t] + 1):
            msgs += [{"role": "user", "content": stream[i]["content"]} for at, i in restored if at == k and present[i]]
            if k == upto[t]:
                break
            m = stream[k]
            if k in present and not present[k] and not any(i == k for _, i in restored):
                m = {**m, "content": marker(m)}
            msgs.append(m)
        return prompt_ids(tok, {"messages": msgs, "tools": opts["tools"], **opts["template"]})

    def position(i):                               # where the item sits now: re-shown copies sit later
        return next((at for at, x in restored if x == i), i)

    def fit(t, ids):
        """Evict the minimum needed for call t to fit; the ids, or None when it cannot."""
        nonlocal evictions
        candidates = [i for i in items if i < upto[t] and present[i] and
                      (policy != "knowing" or not any(u >= t for u in uses.get(i, [])))]
        if policy == "oldest":
            candidates.sort(key=lambda i: arrival[i])
        else:
            candidates.sort(key=position, reverse=True)
        P = len(ids)
        for i in candidates:
            if P <= limit:
                break
            present[i] = False
            restored[:] = [(at, x) for at, x in restored if x != i]
            P -= item_tokens[i] - marker_tokens[i]
            evictions += 1
            if P <= limit:                         # check the estimate against the rebuilt prompt
                ids = build(t)
                P = len(ids)
        ids = build(t)
        return ids if len(ids) <= limit else None

    for t in range(len(upto)):
        if policy not in ("none", "stop"):
            for i in [i for i in items if i < upto[t] and not present[i] and t in uses.get(i, [])]:
                ids = build(t)                     # the extra call that re-shows it (it must fit too)
                if len(ids) > limit and (ids := fit(t, ids)) is None:
                    return prompts, gens, refetches, t, evictions
                prompts.append(ids)
                gens.append(median_G)
                refetches += 1
                present[i] = True
                restored.append((upto[t], i))
        ids = build(t)
        if len(ids) > limit and policy != "none":
            if policy == "stop" or (ids := fit(t, ids)) is None:
                return prompts, gens, refetches, t, evictions
        prompts.append(ids)
        gens.append(G[t])
    return prompts, gens, refetches, None, evictions


def flops(prompts, gens, ct, ca) -> float:
    R = cached_prefix(prompts)
    return sum(sum(eq9(len(p), r, g, ct, ca)) for p, r, g in zip(prompts, R, gens))


def analyse(job) -> dict:
    meta, bench = job
    tok = tokenizer(MEASURED)
    k = load(MEASURED)
    ct, ca = k["C_token"], k["C_attn"]
    rows = [r for r in map(json.loads, [x for x in (b.ROOT / meta["calls_log"]).read_text().split("\n") if x])
            if r.get("call_no") and r.get("status") == 200 and isinstance(r.get("response"), dict)
            and "completion_tokens" in (r.get("usage") or {}) and r.get("kind", "agent") != "summary"]
    if not rows:
        return {"run": {"run_id": meta["run_id"], "skipped": "no agent calls"}, "items": []}
    replies = [(r["response"].get("choices") or [{}])[0].get("message") or {} for r in rows]
    G = [r["usage"]["completion_tokens"] for r in rows]
    req0 = rows[0]["request"]
    opts = {"tools": req0.get("tools") or None,
            "template": {k2: req0[k2] for k2 in ("chat_template_kwargs", "reasoning_effort") if k2 in req0}}
    stream, upto = stream_of(rows)
    arrival = {}
    for t, n in enumerate(upto):
        for i in range(upto[t - 1] if t else 0, n):
            arrival[i] = t
    producer = {}                          # tool_call_id -> (tool name, arguments text)
    for m in stream:
        for call in m.get("tool_calls") or []:
            fn = call.get("function") or {}
            producer[call.get("id")] = (fn.get("name"), fn.get("arguments") or "")
    item_tokens = {}
    for i, m in enumerate(stream):
        if m.get("role") != "tool":
            continue
        text = b.text_of(m.get("content"))
        if bench == "bcp":                 # every search result and opened document (CLM calls them through bash)
            name, args = producer.get(m.get("tool_call_id"), (None, ""))
            if name in ("bcp_search", "bcp_get_document") or "bcp_search" in args or "bcp_get_document" in args:
                item_tokens[i] = count(text)
        elif count(b.clean(text)) >= b.BIG:
            item_tokens[i] = count(text)
    items = sorted(item_tokens)
    uses = uses_of(stream, upto, replies, items, bench)
    # check the rebuild on arms with no context management
    rebuild_ok = None
    if meta["arm"] in NO_MANAGEMENT:
        logged = [prompt_ids(tok, r["request"]) for r in rows]
        rebuilt = replay("none", stream, upto, items, item_tokens, uses, arrival, G, LIMITS[bench], opts, tok, 0)[0]
        rebuild_ok = sum(x == y for x, y in zip(logged, rebuilt)) / len(logged)
    # did the logged trajectory reach its answer? (our submit, CLM's submit command; on BrowseComp-Plus, an answer file)
    answered = any((c.get("function") or {}).get("name") == "submit" or "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" in
                   ((c.get("function") or {}).get("arguments") or "") for r in replies for c in r.get("tool_calls") or [])
    if bench == "bcp":
        answered = bool(meta.get("answer_file"))
    median_G = int(statistics.median(G))
    out = {}
    for policy in POLICIES:
        prompts, gens, refetches, stopped, evictions = replay(policy, stream, upto, items, item_tokens, uses, arrival, G,
                                                              LIMITS[bench], opts, tok, median_G)
        out[policy] = {"F": flops(prompts, gens, ct, ca), "refetches": refetches, "stopped_at": stopped,
                       "evictions": evictions, "survived": stopped is None and answered,
                       "peak": max(map(len, prompts)) if prompts else 0, "calls": len(prompts)}
    actual = measure_log(b.ROOT / meta["calls_log"], tok, ct, ca)["totals"]
    run = {"label": meta["label"], "arm": meta["arm"], "task": meta.get("task"), "run_id": meta["run_id"],
           "agent_calls": len(rows), "items": len(items), "items_needed_again": sum(1 for i in items if uses.get(i)),
           "rebuild_matches": rebuild_ok, "median_G": median_G, "answered": answered,
           "included": out["none"]["peak"] > LIMITS[bench],
           "actual_F": actual["F"], "actual_F_server": actual["F_server"], "actual_valid": actual["valid"],
           "actual_calls": actual["calls"]}
    if bench == "bcp":                    # repeats: results the question already had; was the first copy in the prompt?
        returned, repeats, still_shown, results = set(), 0, 0, 0
        for t, row in enumerate(rows):
            msgs = row["request"]["messages"]
            for j, m in enumerate(msgs):
                k = next((i for i in items if stream[i] is not None and b.identity(stream[i]) == b.identity(m)
                          and arrival[i] == t), None)
                if k is None or (producer.get(m.get("tool_call_id"), ("",))[0] or "") .endswith("get_document"):
                    continue
                earlier = "\n".join(b.text_of(x.get("content")) for x in msgs[:j] if x.get("role") == "tool")
                for docid in DOCID.findall(b.text_of(m.get("content"))):
                    results += 1
                    if docid in returned:
                        repeats += 1
                        still_shown += f'"docid": "{docid}"' in earlier
                    returned.add(docid)
        run.update(search_results=results, repeat_results=repeats, repeat_first_copy_in_prompt=still_shown)
    for policy, v in out.items():
        for key, val in v.items():
            run[f"{policy}_{key}"] = val
    item_rows = [{"run_id": meta["run_id"], "arm": meta["arm"], "stream_index": i, "arrival_call": arrival[i],
                  "tokens": item_tokens[i], "needed_again": bool(uses.get(i)),
                  "uses": " ".join(map(str, uses.get(i, []))),
                  "first_use_turns_later": (uses[i][0] - arrival[i] + 1) if uses.get(i) else None} for i in items]
    return {"run": run, "items": item_rows}


def summarise(bench: str, results: list[dict]) -> None:
    runs = [r["run"] for r in results if "skipped" not in r["run"]]
    items = [i for r in results for i in r["items"]]
    inc = [r for r in runs if r["included"]]
    pct = lambda x, z: f"{100 * x / z:.1f}%" if z else "–"   # noqa: E731
    F = {p: sum(r[f"{p}_F"] for r in inc) for p in POLICIES}
    alive = {p: sum(bool(r[f"{p}_survived"]) for r in inc) for p in POLICIES}
    later = [i["first_use_turns_later"] for i in items if i["first_use_turns_later"] is not None]
    q = statistics.quantiles(later, n=4) if len(later) >= 4 else [None, None, None]
    rel = lambda p: pct(F[p] - F["none"], F["none"])   # noqa: E731
    out = [f"# Step 13b: {bench}, forced eviction, a replay of fixed trajectories (not live behaviour)", "",
           f"Limit {LIMITS[bench]:,} tokens. {len(runs)} runs; **{len(inc)} included** (their stream puts some call over "
           f"the limit); {sum(r['answered'] for r in inc)} of them reached an answer as logged.", "",
           "## The answer (included runs; FLOPs against the stream with no eviction, which passes the limit)", "",
           "| policy | FLOPs vs no eviction | runs reaching their answer | re-fetches | evictions |", "|---|---|---|---|---|"]
    for p in POLICIES:
        out.append(f"| {p} | {rel(p) if p != 'none' else '(reference)'} | {alive[p]}/{len(inc)} | "
                   f"{sum(r[f'{p}_refetches'] for r in inc)} | {sum(r[f'{p}_evictions'] for r in inc)} |")
    out += ["",
            f"- **Gap (a) oldest -> (b) cheapest: {pct(F['oldest'] - F['cheapest'], F['none'])} of no-eviction FLOPs; "
            f"{alive['cheapest'] - alive['oldest']:+d} runs reaching their answer.**",
            f"- **Gap (b) cheapest -> (c) knowing: {pct(F['cheapest'] - F['knowing'], F['none'])}; "
            f"{alive['knowing'] - alive['cheapest']:+d} runs reaching their answer.** (the room a forecaster has)",
            f"- Base's rule on the same trajectories (stop): {rel('stop')} FLOPs, {alive['stop']}/{len(inc)} reach their answer.", "",
            "## Items needed again (all runs)", "",
            f"- {sum(i['needed_again'] for i in items)} of {len(items)} items are needed again "
            f"({pct(sum(i['needed_again'] for i in items), len(items))}).",
            f"- First need, in turns after arrival: median {statistics.median(later) if later else '–'}, quartiles "
            f"{q[0]}-{q[2]}, range {min(later) if later else '–'}-{max(later) if later else '–'}.", ""]
    if bench == "bcp":
        out += [f"- Repeats: {pct(sum(r.get('repeat_results', 0) for r in runs), sum(r.get('search_results', 0) for r in runs))} "
                f"of search results were documents the question already had; the first copy was still in the prompt for "
                f"{pct(sum(r.get('repeat_first_copy_in_prompt', 0) for r in runs), sum(r.get('repeat_results', 0) for r in runs))} "
                "of them.", ""]
    out += ["## Per arm (included runs)", "",
            "| step | arm | included | answered as logged | none | stop | oldest | cheapest | knowing | gap oldest->cheapest | gap cheapest->knowing |",
            "|---|---|---|---|---|---|---|---|---|---|---|"]
    groups = defaultdict(list)
    for r in inc:
        groups[(b.LABELS.get(r["label"], r["label"]), r["arm"])].append(r)
    for g in sorted(groups):
        rs = groups[g]
        Fn = sum(r["none_F"] for r in rs)
        cell = lambda p: f"{pct(sum(r[f'{p}_F'] for r in rs) - Fn, Fn)} ({sum(bool(r[f'{p}_survived']) for r in rs)})"   # noqa: E731
        out.append(f"| {g[0]} | {g[1]} | {len(rs)} | {sum(r['answered'] for r in rs)} | {Fn / 1e15:.2f} PF | {cell('stop')} | "
                   f"{cell('oldest')} | {cell('cheapest')} | {cell('knowing')} | "
                   f"{pct(sum(r['oldest_F'] - r['cheapest_F'] for r in rs), Fn)} | {pct(sum(r['cheapest_F'] - r['knowing_F'] for r in rs), Fn)} |")
    out += ["", "Cells: FLOPs against no eviction (runs reaching their answer).", "",
            "## What base and summary actually did on the same tasks", "",
            "| task | arm (actual) | runs | PFLOPs (canonical) | PFLOPs on Purdue's ids (variant) | reached an answer | replayed policies on the same task: "
            "PFLOPs (rebuild) and answers, oldest / cheapest / knowing |", "|---|---|---|---|---|---|---|"]
    actual_arms = ("raw-32k", "base-32k", "base", "summary-32k")
    by_task = defaultdict(list)
    for r in inc:
        by_task[r["task"]].append(r)
    for r_task in sorted({r["task"] for r in runs if r["arm"] in actual_arms and r["task"] in by_task}):
        rep = by_task[r_task]
        replayed = " / ".join(f"{sum(x[f'{p}_F'] for x in rep) / 1e15 / len(rep):.2f} ({sum(bool(x[f'{p}_survived']) for x in rep)}/{len(rep)})"
                              for p in ("oldest", "cheapest", "knowing"))
        for arm in actual_arms:
            act = [r for r in runs if r["task"] == r_task and r["arm"] == arm]
            if act:
                out.append(f"| {r_task} | {arm} | {len(act)} | {sum(x['actual_F'] for x in act) / 1e15 / len(act):.2f} | "
                           f"{sum((x['actual_F_server'] or 0) for x in act) / 1e15 / len(act):.2f} | {sum(x['answered'] for x in act)}/{len(act)} | "
                           f"{replayed} (over {len(rep)} replayed runs) |")
    (OUT / bench / "summary.md").write_text("\n".join(out) + "\n")
    print("\n".join(out))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("benchmark", choices=("tblite", "bcp"))
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--only")
    ap.add_argument("--labels", default="tc9,tc10s,tc11v")
    a = ap.parse_args()
    if a.benchmark == "tblite":
        metas = [m for m in b.metas() if m["label"] in a.labels.split(",")]
    else:                                 # dropped in step 14a (Kavin): 14b's re-read analysis replaces it
        raise SystemExit("the BrowseComp-Plus part of 13b was dropped in step 14a; see step 14b")
        # the 13a pilot's trajectories (outside the repo)
        metas = []
        for path in sorted((Path.home() / "econo" / "bcp" / "runs").glob("*/*/*/summary.json")):
            s = json.loads(path.read_text())
            if s.get("skipped") or not s.get("calls_log") or not Path(s["calls_log"]).exists():
                continue
            judged = {}
            verdicts = path.parents[2] / "judge.jsonl"
            if verdicts.exists():
                judged = {v["run_id"]: v for v in map(json.loads, (x for x in verdicts.read_text().split("\n") if x))}
            metas.append({"label": "13a pilot", "run_id": s["run_id"], "arm": s["arm"], "task": s["task"],
                          "calls_log": s["calls_log"],
                          "answer_file": (judged.get(s["run_id"]) or {}).get("has_answer_file")})
    metas = [m for m in metas if not a.only or a.only in m["run_id"]]
    (OUT / a.benchmark).mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(a.workers) as pool:
        results = list(pool.map(analyse, [(m, a.benchmark) for m in metas]))
    b.OUT = OUT / a.benchmark
    b.write("runs.csv", [r["run"] for r in results])
    b.write("items.csv", [i for r in results for i in r["items"]])
    summarise(a.benchmark, results)


if __name__ == "__main__":
    main()
