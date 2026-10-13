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

Replay under the limit (TBLite: 29,952 = 32,000 - 2,048; BrowseComp-Plus: 23,560 - 2,048 = 21,512). Before each
call, if its prompt would pass 80% of the limit, items are evicted in one batch until it is at most 60% (or no
candidate is left); an evicted item becomes a one-line marker:
  (a) no eviction (the stream as is);
  (b) oldest first: any item, in arrival order;
  (c) knowing: only items never needed again from this call on, oldest first.
A later need for an evicted item costs one extra call before the call that needs it, whose prompt is the context
at that moment and whose output re-shows the item (appended at the end, where it then stays). ASSUMPTIONS: the
extra call generates the run's median G; every logged call keeps its logged G; the re-shown item is the same
text. Every prompt is rebuilt with the model's chat template and priced with Eq. 9 and the cache simulator, so
the re-prefill after each eviction is counted.

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
from measure.flops import eq9, prompt_ids  # noqa: E402
from measure.models import MEASURED, load  # noqa: E402
from measure.tokens import count_text, tokenizer  # noqa: E402

OUT = b.ROOT / "runs" / "tier-a" / "step13b"
LIMITS = {"tblite": 29_952, "bcp": 21_512}
HIGH, LOW = 0.8, 0.6
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
    """The prompts (token ids) and Gs of the replayed run under one policy, and its counts."""
    present = {i: True for i in items}            # item -> shown in full at its place
    restored: list[tuple[int, int]] = []          # (stream position it was re-shown at, item), in order
    prompts, gens, refetches, over = [], [], 0, 0
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

    for t in range(len(upto)):
        if policy != "a":
            gone = [i for i in items if i < upto[t] and not present[i] and t in uses.get(i, [])]
            for i in gone:                          # an extra call re-shows it
                prompts.append(build(t))
                gens.append(median_G)
                refetches += 1
                present[i] = True                   # now shown again, after the messages of call t
                restored.append((upto[t], i))
        ids = build(t)
        if policy != "a" and len(ids) > HIGH * limit:
            P = len(ids)
            candidates = [i for i in items if i < upto[t] and present[i] and
                          (policy == "b" or not any(u >= t for u in uses.get(i, [])))]
            candidates.sort(key=lambda i: arrival[i])
            for i in candidates:
                if P <= LOW * limit:
                    break
                present[i] = False
                restored[:] = [(at, x) for at, x in restored if x != i]
                P -= item_tokens[i] - marker_tokens[i]
            ids = build(t)
        over += len(ids) > limit
        prompts.append(ids)
        gens.append(G[t])
    return prompts, gens, refetches, over


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
        rebuilt = replay("a", stream, upto, items, item_tokens, uses, arrival, G, LIMITS[bench], opts, tok, 0)[0]
        rebuild_ok = sum(x == y for x, y in zip(logged, rebuilt)) / len(logged)
    median_G = int(statistics.median(G))
    out = {}
    for policy in ("a", "b", "c"):
        prompts, gens, refetches, over = replay(policy, stream, upto, items, item_tokens, uses, arrival, G,
                                                LIMITS[bench], opts, tok, median_G)
        out[policy] = {"F": flops(prompts, gens, ct, ca), "refetches": refetches, "over_limit": over,
                       "peak": max(map(len, prompts)), "calls": len(prompts)}
    run = {"label": meta["label"], "arm": meta["arm"], "task": meta.get("task"), "run_id": meta["run_id"],
           "agent_calls": len(rows), "items": len(items), "items_needed_again": sum(1 for i in items if uses.get(i)),
           "rebuild_matches": rebuild_ok, "median_G": median_G}
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
    pct = lambda x, z: f"{100 * x / z:.1f}%" if z else "–"   # noqa: E731
    groups = defaultdict(list)
    for r in runs:
        groups[(b.LABELS.get(r["label"], r["label"]), r["arm"])].append(r)
    order = sorted(groups, key=lambda g: (["step 9", "10d", "step 11", "13a pilot"].index(g[0])
                                          if g[0] in ("step 9", "10d", "step 11", "13a pilot") else 9, g[1]))
    F = {p: sum(r[f"{p}_F"] for r in runs) for p in "abc"}
    later = [i["first_use_turns_later"] for i in items if i["first_use_turns_later"] is not None]
    q = statistics.quantiles(later, n=4) if len(later) >= 4 else [None, None, None]
    out = [f"# Step 13b: {bench}, a replay of fixed trajectories (not live behaviour)", "",
           f"Limit {LIMITS[bench]:,} tokens; evict at 80% down to 60%. {len(runs)} runs, {len(items)} items.", "",
           "## The answer", "",
           f"- FLOPs saved against (a) no eviction: (b) oldest first **{pct(F['a'] - F['b'], F['a'])}**; "
           f"(c) knowing **{pct(F['a'] - F['c'], F['a'])}**.",
           f"- Re-fetches caused by (b): **{sum(r['b_refetches'] for r in runs)}** extra calls "
           f"(by (c): {sum(r['c_refetches'] for r in runs)}).",
           f"- **The gap between (b) and (c): {pct(F['b'] - F['c'], F['a'])} of (a)'s FLOPs** - the room a forecaster has.",
           f"- Calls over the limit: (a) {sum(r['a_over_limit'] for r in runs)}, (b) {sum(r['b_over_limit'] for r in runs)}, "
           f"(c) {sum(r['c_over_limit'] for r in runs)} (of {sum(r['a_calls'] for r in runs)} calls in (a)).", "",
           "## Items needed again", "",
           f"- {sum(i['needed_again'] for i in items)} of {len(items)} items are needed again "
           f"({pct(sum(i['needed_again'] for i in items), len(items))}); never again: "
           f"{pct(sum(not i['needed_again'] for i in items), len(items))}.",
           f"- First need, in turns after the item arrived: median {statistics.median(later) if later else '–'}, "
           f"quartiles {q[0]}-{q[2]}, range {min(later) if later else '–'}-{max(later) if later else '–'}.", "",
           *([f"- Repeats: {pct(sum(r.get('repeat_results', 0) for r in runs), sum(r.get('search_results', 0) for r in runs))} "
              f"of search results were documents the question already had; the first copy was still in the prompt for "
              f"{pct(sum(r.get('repeat_first_copy_in_prompt', 0) for r in runs), sum(r.get('repeat_results', 0) for r in runs))} "
              f"of them.", ""] if bench == "bcp" else []),
           "## Per arm", "",
           "| step | arm | runs | items (needed again) | (b) saves | (c) saves | gap (b)-(c) | (b) re-fetches | "
           "over limit a/b/c | rebuild matches the logged prompts |", "|---|---|---|---|---|---|---|---|---|---|"]
    for g in order:
        rs = groups[g]
        Fa = sum(r["a_F"] for r in rs)
        m = [r["rebuild_matches"] for r in rs if r["rebuild_matches"] is not None]
        out.append(f"| {g[0]} | {g[1]} | {len(rs)} | {sum(r['items'] for r in rs)} ({sum(r['items_needed_again'] for r in rs)}) | "
                   f"{pct(Fa - sum(r['b_F'] for r in rs), Fa)} | {pct(Fa - sum(r['c_F'] for r in rs), Fa)} | "
                   f"{pct(sum(r['b_F'] - r['c_F'] for r in rs), Fa)} | {sum(r['b_refetches'] for r in rs)} | "
                   f"{sum(r['a_over_limit'] for r in rs)}/{sum(r['b_over_limit'] for r in rs)}/{sum(r['c_over_limit'] for r in rs)} | "
                   + (f"{statistics.mean(m):.1%} of calls (mean over {len(m)} runs)" if m else "– (managed arm)") + " |")
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
    else:                                 # the 13a pilot's trajectories (outside the repo)
        metas = []
        for path in sorted((Path.home() / "econo" / "bcp" / "runs").glob("*/*/*/summary.json")):
            s = json.loads(path.read_text())
            if s.get("skipped") or not s.get("calls_log") or not Path(s["calls_log"]).exists():
                continue
            metas.append({"label": "13a pilot", "run_id": s["run_id"], "arm": s["arm"], "task": s["task"],
                          "calls_log": s["calls_log"]})
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
