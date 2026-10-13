"""Step 17: context policies replayed on fixed trajectories, priced in FLOPs and in API dollars. Offline, no model
calls, no code change outside analysis scripts. FIXED-TRAJECTORY ESTIMATES on the canonical track.

Traces:
  - TBLite, all arms (tc9, tc10s, tc11v: 130 runs) and the BrowseComp-Plus CLM pilot (bcp13p: 8 runs), from the
    gateway logs: the header (system prompt with tools, and the task), then per agent call its reply (content and
    tool calls, reasoning removed as our gateway removes it) and that reply's tool outputs as first shown.
    Management and harness messages (summaries, CLM's re-serialized turns and notices, nudges) are not part of the
    trace: every policy starts from the bare conversation. Summary calls are left out; generation G is the logged
    completion tokens.
  - The released BrowseComp-Plus GPT-5 BM25 runs (830 questions; ~/econo/bcp, never written to the repo), tokenized
    with our Qwen tokenizer and priced with Qwen's constants: a turn per tool call and one for the final answer;
    G is the Qwen count of what the run file shows (reasoning summaries, call arguments, the answer), an
    underestimate since GPT-5's hidden reasoning is not released; the header is our BrowseComp-Plus base header
    with the question.
  - Also, as a contrast, our BrowseComp-Plus base pilot runs (4).
Needed again (13b's rule, with step 16's names): a later reply (reasoning, content, tool-call arguments) quotes 30+
characters of the observation, or names something distinctive from it (path, code-like identifier, docid, multi-word
proper name), that no other message in that call's context contains.

Policies, each under the same budget (TBLite 29,952; BrowseComp-Plus 21,512):
  P0 keep everything (no budget; the reference).
  P1 as executed: the logged prompts (management, summaries and nudges included; the released runs: = P0).
  P2 rolling observation masking (The Complexity Trap), M = 10: observations older than 10 turns become
     "[N lines omitted]".
  P3 block masking: the same rule, applied only every k = 5 calls (the prompt stays stable in between).
  P4 decide at age a in {1, 2}: an observation becomes a stub after a replies have seen it, if
     simple signal: none of those a replies referenced it ("referenced by the next reply or not" for a = 1);
     oracle: no later reply references it (no restore is ever needed).
  P5 two-zone (LSM-like): nothing is touched until the budget forces it; then one compaction stubs the observations
     with the least text after them (the tail working zone; the prefix stays an append-only archive) down to 60% of
     the limit, so compactions are rare.
  Shared rules: an observation needed again (referenced by the reply of call u) while stubbed is restored in full at
  the tail of call u's prompt (its tokens charged there, and later policies apply to the copy). When a rule leaves a
  prompt over the limit (P2-P4), the oldest full observations are stubbed until it fits (a fallback, counted).
  Observations from the previous turn and those needed by the current reply are never stubbed by the budget.
  A call that cannot fit is counted as over the limit.
Pricing per call: Eq. 9 (measure.flops.eq9) with a vLLM-style prefix cache at item granularity (step 15); also
new-prefill tokens (P - R), tokens sent (P), and API dollars in input-token units: uncached input 1, cache read
0.1, output 6 (per token).
The 68 cut replies (step 16 (f)): their discarded thinking, and whether the next reply's first 200 tokens of
thinking repeat the cut one's start (difflib ratio on Qwen token ids), against a baseline: the same comparison with a
random reply of the same run more than 2 calls away (seeded).

Use: PYTHONPATH=. .venv/bin/python runs/tier-a/scripts/step17_policies.py [--workers 5] [--only SUBSTRING]
Output: runs/tier-a/step17/{runs,cut}.csv
"""

import argparse
import csv
import difflib
import json
import random
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import step12b_analysis as b  # noqa: E402
import step12c_analysis as c12  # noqa: E402
import step14b_rereads as r14  # noqa: E402
import step16_waste as s16  # noqa: E402

from measure.flops import eq9, measure_log  # noqa: E402
from measure.models import MEASURED, load  # noqa: E402
from measure.tokens import count_text, tokenizer  # noqa: E402

OUT = b.ROOT / "runs" / "tier-a" / "step17"
RELEASED = Path.home() / "econo" / "bcp" / "BrowseComp-Plus" / "data"
LIMITS = {"tblite": 29_952, "bcp": 21_512}
WRAP, GEN, BLOCK = 5, 3, 16
M, K, LOW_WATER = 10, 5, 0.6
CACHE_READ, OUTPUT = 0.1, 6.0
POLICIES = ["P0", "P2", "P3", "P4 a=1 simple", "P4 a=1 oracle", "P4 a=2 simple", "P4 a=2 oracle", "P5"]


def count(text: str) -> int:
    return count_text(text, MEASURED) if text else 0


def stub_tokens(lines: int, cache={}) -> int:
    if lines not in cache:
        cache[lines] = count(f"[{lines} lines omitted]") + WRAP
    return cache[lines]


def references(messages: list[tuple[int, str, int | None]], reply_texts: list[str], bench: str) -> list[list[int]]:
    """For each observation, the replies that need it again (13b's rule, with step 16's names): reply u quotes a
    30-character window of it, or names something distinctive from it (a path, a code-like identifier, a docid, a
    multi-word proper name), that no other message in call u's context contains. messages: (turn, text, observation
    id or None) in context order; turn -1 is the task; a message of turn s is in the context of calls u > s."""
    n_obs = 1 + max((o for _, _, o in messages if o is not None), default=-1)
    out = [[] for _ in range(n_obs)]
    count, owner = defaultdict(int), {}
    k = 0
    for u, said in enumerate(reply_texts):
        while k < len(messages) and messages[k][0] < u:
            _, text, o = messages[k]
            body = r14.body_of(text)[0] if o is not None else text
            for g in s16.windows(body) | s16.distinctive(body, bench):
                count[g] += 1
                owner[g] = o
            k += 1
        hit = {owner[g] for g in s16.windows(said) | {nm for nm in s16.distinctive(said, bench)}
               if count.get(g) == 1 and owner.get(g) is not None}
        for o in hit:
            out[o].append(u)
    return out


def trace_from_log(meta: dict, tok) -> dict:
    rows = r14.load_rows(Path(meta["calls_log"]) if Path(meta["calls_log"]).is_absolute() else b.ROOT / meta["calls_log"])
    agent = [r for r in rows if r.get("kind", "agent") != "summary"]
    replies = [(r["response"].get("choices") or [{}])[0].get("message") or {} for r in agent]
    first = agent[0]["request"]
    text = c12.full_text(tok, {**first, "messages": first["messages"][:2]})
    header = len(tok(text, add_special_tokens=False)["input_ids"]) - GEN
    by_call = defaultdict(list)                      # (id, arguments) -> reply indices
    for t, rep in enumerate(replies):
        for j, c in enumerate(rep.get("tool_calls") or []):
            fn = c.get("function") or {}
            by_call[(c.get("id"), fn.get("arguments"))].append((t, j))
    outputs = {}                                     # (t, j) -> text, as first shown
    for k, r in enumerate(agent):
        msgs = r["request"]["messages"]
        for i, m in enumerate(msgs):
            if m["role"] != "tool":
                continue
            a = next((msgs[x] for x in range(i - 1, -1, -1) if msgs[x]["role"] == "assistant" and msgs[x].get("tool_calls")), None)
            call = next((c for c in (a or {}).get("tool_calls") or [] if c.get("id") == m.get("tool_call_id")), None)
            if call is None:
                continue
            cands = [tj for tj in by_call.get((call.get("id"), (call.get("function") or {}).get("arguments")), []) if tj[0] < k]
            if cands and cands[-1] not in outputs:
                outputs[cands[-1]] = b.text_of(m.get("content"))
    turns, obs_texts, reply_texts = [], [], []
    messages = [(-1, b.text_of(first["messages"][1].get("content")) if len(first["messages"]) > 1 else "", None)]
    for t, rep in enumerate(replies):
        calls = rep.get("tool_calls") or []
        messages.append((t, "\n".join([b.text_of(rep.get("content"))] + [(c.get("function") or {}).get("arguments") or "" for c in calls]), None))
        a = WRAP + count(b.text_of(rep.get("content"))) + sum(
            WRAP + count(f"{(c.get('function') or {}).get('name', '')} {(c.get('function') or {}).get('arguments') or ''}") for c in calls)
        obs = []
        for j in range(len(calls)):
            if (t, j) in outputs:
                o = outputs[(t, j)]
                obs.append({"tok": count(o) + WRAP, "stub": stub_tokens(o.count("\n") + 1), "id": len(obs_texts)})
                messages.append((t, o, len(obs_texts)))
                obs_texts.append((t, o))
        turns.append({"G": agent[t]["usage"]["completion_tokens"], "a": a, "obs": obs})
        reply_texts.append("\n".join([rep.get("reasoning_content") or rep.get("reasoning") or "", b.text_of(rep.get("content"))]
                                     + [(c.get("function") or {}).get("arguments") or "" for c in calls]))
    missing = sum(len(rep.get("tool_calls") or []) for rep in replies[:-1]) - len(obs_texts)
    return {"name": meta["run_id"], "bench": meta["bench"], "source": "tblite" if meta["bench"] == "tblite" else "bcp-clm" if meta["arm"].startswith("clm") else "bcp-base",
            "arm": meta["arm"], "H": header, "turns": turns, "refs": references(messages, reply_texts, meta["bench"]),
            "missing_outputs": max(missing, 0), "calls_log": meta["calls_log"]}


def trace_from_released(q: dict, question: str, header0: int) -> dict:
    turns, obs_texts, reply_texts, thought = [], [], [], ""
    messages = [(-1, question, None)]
    for r in q["result"]:
        if r["type"] == "reasoning":
            thought += "\n".join(x.get("text", "") if isinstance(x, dict) else str(x) for x in r.get("output") or [])
        elif r["type"] == "tool_call":
            args = r.get("arguments") or ""
            out = r["output"] if isinstance(r["output"], str) else json.dumps(r["output"])
            turns.append({"G": count(thought) + count(args) + 5, "a": WRAP + WRAP + count(f"{r.get('tool_name')} {args}"),
                          "obs": [{"tok": count(out) + WRAP, "stub": stub_tokens(out.count("\n") + 1), "id": len(obs_texts)}]})
            messages.append((len(turns) - 1, args, None))
            messages.append((len(turns) - 1, out, len(obs_texts)))
            obs_texts.append((len(turns) - 1, out))
            reply_texts.append(thought + "\n" + args)
            thought = ""
        elif r["type"] == "output_text":
            ans = r["output"] if isinstance(r["output"], str) else json.dumps(r["output"])
            turns.append({"G": count(thought) + count(ans), "a": 0, "obs": []})
            reply_texts.append(thought + "\n" + ans)
            thought = ""
    return {"name": f"gpt5-bm25.{q['query_id']}", "bench": "bcp", "source": "bcp-gpt5", "arm": "gpt-5",
            "H": header0 + count(question), "turns": turns, "refs": references(messages, reply_texts, "bcp"),
            "missing_outputs": 0, "calls_log": None}


def replay(tr: dict, policy: str, ct: float, ca: float) -> dict:
    turns, refs, limit = tr["turns"], tr["refs"], LIMITS[tr["bench"]]
    n = len(turns)
    needed = defaultdict(set)
    for o, us in enumerate(refs):
        for u in us:
            needed[u].add(o)
    tok_of = {}
    for s, tn in enumerate(turns):
        for ob in tn["obs"]:
            tok_of[ob["id"]] = (ob["tok"], ob["stub"])
    items = []                                       # {"src", "slot", "full"}: originals, then restored copies
    by_src = defaultdict(list)
    seen, res = set(), {"F": 0.0, "U": 0, "sent": 0, "dollars": 0.0, "restores": 0, "compactions": 0, "over": 0}
    a = int(policy.split("a=")[1][0]) if policy.startswith("P4") else None

    def build(t):
        seq = [(("H",), tr["H"])]
        slots = defaultdict(list)
        for it in items:
            if it["slot"] < t:
                slots[it["slot"]].append(it)
        for s in range(t):
            seq.append(((("a", s)), turns[s]["a"]))
            for it in slots[s]:
                full, stub = tok_of[it["src"]]
                seq.append(((id(it), it["full"]), full if it["full"] else stub))
        return seq

    def size(seq):
        return sum(x[1] for x in seq) + GEN

    for t in range(n):
        if t > 0:
            for ob in turns[t - 1]["obs"]:
                it = {"src": ob["id"], "slot": t - 1, "full": True}
                items.append(it)
                by_src[ob["id"]].append(it)
        visible = [it for it in items if it["slot"] < t]
        for o in needed[t]:                          # restore what this call's reply needs
            if by_src[o] and not any(it["full"] for it in by_src[o]):
                it = {"src": o, "slot": t - 1, "full": True}
                items.append(it)
                by_src[o].append(it)
                res["restores"] += 1
        visible = [it for it in items if it["slot"] < t]
        for it in visible:
            age = t - 1 - it["slot"]
            if not it["full"]:
                continue
            if policy == "P2" and age >= M:
                it["full"] = False
            elif policy == "P3" and t % K == 0 and age >= M:
                it["full"] = False
            elif a is not None and age == a:
                later = [u for u in refs[it["src"]] if u >= t]
                window = [u for u in refs[it["src"]] if it["slot"] < u < t]
                if ("oracle" in policy and not later) or ("simple" in policy and not window):
                    it["full"] = False
        seq = build(t)
        if policy != "P0" and size(seq) > limit:
            cands = [it for it in visible if it["full"] and it["slot"] < t - 1 and it["src"] not in needed[t]]
            if policy == "P5":
                cands.sort(key=lambda it: (it["slot"], items.index(it)), reverse=True)   # least text after first
                target = LOW_WATER * limit
            else:
                cands.sort(key=lambda it: (it["slot"], items.index(it)))                  # oldest first
                target = limit
            P = size(seq)
            for it in cands:
                if P <= target:
                    break
                full, stub = tok_of[it["src"]]
                it["full"] = False
                P -= full - stub
            res["compactions"] += 1
            seq = build(t)
        P = size(seq)
        res["over"] += policy != "P0" and P > limit
        h, cum, R, matched, hashes = 0, 0, 0, True, []
        for key, k in seq:
            h = hash((h, key))
            cum += k
            hashes.append(h)
            if matched and h in seen:
                R = cum
            else:
                matched = False
        R = min(BLOCK * (R // BLOCK), BLOCK * ((P - 1) // BLOCK))
        seen.update(hashes)
        G = turns[t]["G"]
        res["F"] += sum(eq9(P, R, G, ct, ca))
        res["U"] += P - R
        res["sent"] += P
        res["dollars"] += (P - R) + CACHE_READ * R + OUTPUT * G
    return res


def executed(calls_log, limit, tok, ct, ca) -> dict:
    m = measure_log(Path(calls_log) if Path(calls_log).is_absolute() else b.ROOT / calls_log, tok, ct, ca)
    rows = m["rows"]
    return {"F": m["totals"]["F"], "U": sum(r["U"] for r in rows), "sent": sum(r["P"] for r in rows),
            "dollars": sum(r["U"] + CACHE_READ * r["R"] + OUTPUT * r["G"] for r in rows), "restores": 0,
            "compactions": 0, "over": sum(r["P"] > limit for r in rows)}


def cut_replies(meta: dict, tok, ct, ca) -> list[dict]:
    rows = r14.load_rows(Path(meta["calls_log"]))
    replies = [(r["response"].get("choices") or [{}])[0].get("message") or {} for r in rows]
    out, rng = [], random.Random(17)
    for t, r in enumerate(rows[:-1]):
        ch = (r["response"].get("choices") or [{}])[0]
        if ch.get("finish_reason") != "length" or replies[t].get("tool_calls") or b.text_of(replies[t].get("content")).strip():
            continue
        think = replies[t].get("reasoning_content") or replies[t].get("reasoning") or ""
        nxt = replies[t + 1].get("reasoning_content") or replies[t + 1].get("reasoning") or ""
        x, y = tok(think, add_special_tokens=False)["input_ids"][:200], tok(nxt, add_special_tokens=False)["input_ids"][:200]
        prefix = next((i for i, (p, q) in enumerate(zip(x, y)) if p != q), min(len(x), len(y)))
        others = [u for u in range(len(rows)) if abs(u - t) > 2 and (replies[u].get("reasoning_content") or replies[u].get("reasoning"))]
        z = tok(replies[rng.choice(others)].get("reasoning_content") or "", add_special_tokens=False)["input_ids"][:200] if others else []
        G, P = r["usage"]["completion_tokens"], r["usage"]["prompt_tokens"]
        out.append({"run_id": meta["run_id"], "call": t, "G": G, "thinking_tokens": len(tok(think, add_special_tokens=False)["input_ids"]),
                    "ratio_200": round(difflib.SequenceMatcher(None, x, y, autojunk=False).ratio(), 3), "common_prefix": prefix,
                    "ratio_200_random": round(difflib.SequenceMatcher(None, x, z, autojunk=False).ratio(), 3) if z else "",
                    "next_cut": (rows[t + 1]["response"].get("choices") or [{}])[0].get("finish_reason") == "length"
                    and not replies[t + 1].get("tool_calls"),
                    "F_gen": ct * G + ca * (G * P + 0.5 * G * G)})
    return out


def job(arg) -> list[dict]:
    kind, payload = arg
    tok = tokenizer(MEASURED)
    k = load(MEASURED)
    ct, ca = k["C_token"], k["C_attn"]
    out = []
    if kind == "log":
        tr = trace_from_log(payload, tok)
        results = {"P1": executed(tr["calls_log"], LIMITS[tr["bench"]], tok, ct, ca)}
        cuts = cut_replies(payload, tok, ct, ca) if payload["bench"] == "bcp" and payload["arm"].startswith("clm") else []
        traces = [tr]
    else:
        header0, items = payload
        traces = [trace_from_released(q, question, header0) for q, question in items]
        results, cuts = None, []
    for tr in traces:
        res = dict(results) if results else {}
        for p in POLICIES:
            res[p] = replay(tr, p, ct, ca)
        if "P1" not in res:
            res["P1"] = dict(res["P0"])
        row = {"source": tr["source"], "arm": tr["arm"], "name": tr["name"], "calls": len(tr["turns"]),
               "observations": len(tr["refs"]), "obs_needed_again": sum(bool(r) for r in tr["refs"]),
               "missing_outputs": tr["missing_outputs"]}
        for p, r in res.items():
            for key, v in r.items():
                row[f"{p}|{key}"] = v
        out.append({"row": row, "cuts": cuts})
        cuts = []
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--only")
    ap.add_argument("--released-limit", type=int, default=0, help="first N released questions only (testing)")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    jobs = [("log", m) for m in r14.metas(a.only)]
    if not a.only or "gpt5" in a.only:
        questions = {str(x["query_id"]): x["query"] for x in map(json.loads, (l for l in (RELEASED / "queries_decrypted.jsonl").read_text().split("\n") if l))}
        base = [m for m in r14.metas("bcp13p.base.591")][0]
        first = r14.load_rows(Path(base["calls_log"]))[0]
        header0 = first["usage"]["prompt_tokens"] - count(questions["591"]) - GEN
        lines = [json.loads(x) for x in (RELEASED / "decrypted_run_files" / "bm25" / "gpt5.jsonl").read_text().split("\n") if x]
        if a.released_limit:
            lines = lines[:a.released_limit]
        chunk = 20
        jobs += [("released", (header0, [(q, questions.get(str(q["query_id"]), "")) for q in lines[i:i + chunk]]))
                 for i in range(0, len(lines), chunk)]
        if a.only:
            jobs = [j for j in jobs if j[0] == "released"]
    with ProcessPoolExecutor(a.workers) as pool:
        results = [x for out in pool.map(job, jobs) for x in out]
    rows = [r["row"] for r in results]
    cuts = [c for r in results for c in r["cuts"]]
    name = "runs.csv" if not a.only else "runs-test.csv"
    with (OUT / name).open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    if cuts and not a.only:
        with (OUT / "cut.csv").open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(cuts[0]))
            w.writeheader()
            w.writerows(cuts)
    print(f"{len(rows)} traces, {len(cuts)} cut replies")


if __name__ == "__main__":
    main()
