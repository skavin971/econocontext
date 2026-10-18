"""Step 12d: a fair slicer comparison on the 238 big outputs of step 12c (tc9, tc10s, tc11v: outputs of 1,000+ tokens
as shown). No Purdue calls and no behaviour change. The only network calls are Jev's (TypeSafe), in the `jev` phase.

Chunks: about 300 tokens each, cut at line boundaries (lines are added to a chunk until the next would take
it past 300 tokens; a single longer line is a chunk of its own). Token counts are the served model's own.

Later-used chunks (the oracle), as in 12c: quoted by a later model turn (an exact substring of 30+ characters
that was not in the context before the output), or referenced by a later command (a line number on the same
source path, or a new symbol).

Slicers, each at 25%, 50% and 75% of the output's tokens. Each ranks the chunks; chunks are then kept in rank
order, skipping any that would pass the budget (so the budget is filled as far as whole chunks allow):
- head_tail: first, last, second, second-last, ...;
- bm25_reply: BM25 against the agent's last visible text (the latest non-empty reply text up to the producing
  reply) plus the producing call's command and arguments;
- bm25_task: BM25 against the task text plus the last 3 commands (the arguments of the last 3 tool calls up to
  and including the producing one);
- jev: Jev's existing chunk-relevance question (econocontext/predictor/jev.py, Jev.arrival, unchanged), asked
  on the logged state at that moment: the task, the conversation before the output as the Owner's turns, and
  the output with these chunks as its line ranges; chunks ranked by Jev's probability;
- oracle: the later-used chunks first (in order), then nothing.
Per slicer and budget: tokens saved, and recall (later-used chunks kept / later-used chunks).

FLOPs: for each call, every big output present in its prompt (the tool message with its first-shown content)
is shortened by the tokens its slice drops; P and R shrink accordingly (R by the dropped tokens of outputs
inside the cached prefix) and Eq. 9 is computed again with the same G. This counts the prefill saved when
the output is first computed and the attention saved in every later call (prefill and decode). ASSUMPTION:
a slice that drops a later-used chunk causes no extra call (the agent never has to fetch it again).
The big outputs' share of FLOPs is the same with the whole output dropped.

Phases (each writes into runs/tier-a/step12d/):
  prepare   offline: outputs.jsonl (chunks, later use, rankings except Jev, where each output sits in each prompt)
            and calls.jsonl (P, R, G per call)
  jev       TypeSafe only: one Jev.arrival request per output with 2+ chunks; answers cached in jev.jsonl
            (only probabilities, usage and error types; never the key, never content); a cached output is
            never asked again
  evaluate  offline: slicers.csv (per output), summary.md
Use: PYTHONPATH=. .venv/bin/python runs/tier-a/scripts/step12d_slicers.py prepare|jev|evaluate
"""

import argparse
import bisect
import csv
import json
import os
import sys
import time
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

OUT = b.ROOT / "runs" / "tier-a" / "step12d"
CHUNK_TOKENS = 300
BUDGETS = (25, 50, 75)
SLICERS = ("head_tail", "bm25_reply", "bm25_task", "jev", "oracle")


def count(text: str) -> int:
    return count_text(text, MEASURED)


def chunk_lines(body: str) -> list[tuple[int, int]]:
    """1-based (first, last) line ranges of chunks of about CHUNK_TOKENS tokens, cut at line boundaries."""
    lines = body.split("\n")
    spans, start, size = [], 0, 0
    for i, line in enumerate(lines):
        n = count(line) + 1
        if size and size + n > CHUNK_TOKENS:
            spans.append((start + 1, i))
            start, size = i, 0
        size += n
    spans.append((start + 1, len(lines)))
    return spans


def turns_of(messages: list[dict], max_chars: int) -> list[dict]:
    """The Owner's turns for Jev (econocontext/owner.py before step 12a, from messages), cut from the front."""
    out = []
    for i, m in enumerate(messages):
        turn = {"turn": i, "role": m.get("role")}
        if m.get("content"):
            turn["text"] = m["content"] if isinstance(m["content"], str) else json.dumps(m["content"])
        if m.get("tool_calls"):
            turn["tool_calls"] = [{"name": x["function"]["name"], "arguments": x["function"].get("arguments")}
                                  for x in m["tool_calls"]]
        out.append(turn)
    total, kept = 0, []
    for turn in reversed(out):
        total += len(json.dumps(turn))
        if total > max_chars and kept:
            break
        kept.append(turn)
    return list(reversed(kept))


def call_args(call: dict) -> dict:
    try:
        a = json.loads((call.get("function") or {}).get("arguments") or "{}")
    except ValueError:
        return {}
    return a if isinstance(a, dict) else {}


# prepare ---------------------------------------------------------------------------------------------------------
def prepare_run(meta: dict) -> dict:
    tok = tokenizer(MEASURED)
    rows = [r for r in map(json.loads, (b.ROOT / meta["calls_log"]).read_text().splitlines())
            if r.get("call_no") and r.get("status") == 200 and isinstance(r.get("response"), dict)
            and "completion_tokens" in (r.get("usage") or {})]
    ids = [prompt_ids(tok, r["request"]) for r in rows]
    R = cached_prefix(ids)
    replies = [(r["response"].get("choices") or [{}])[0].get("message") or {} for r in rows]
    calls = [{"run_id": meta["run_id"], "t": t, "kind": r.get("kind", "agent"), "P": len(ids[t]), "R": R[t],
              "G": r["usage"]["completion_tokens"]} for t, r in enumerate(rows)]
    turn_texts = [[x.get("reasoning_content") or x.get("reasoning") or "", b.text_of(x.get("content"))] + b.arg_strings(x)
                  for x in replies]
    turn_calls = [x.get("tool_calls") or [] for x in replies]
    task = b.text_of(rows[0]["request"]["messages"][1].get("content")) if rows else ""
    outputs, first = [], {}
    for t, row in enumerate(rows):
        messages = row["request"]["messages"]
        context = []
        for j, m in enumerate(messages):
            cid = m.get("tool_call_id")
            if m.get("role") == "tool" and cid not in first:
                raw = b.text_of(m.get("content"))
                first[cid] = raw
                body = b.clean(raw)
                tokens = count(body)
                if tokens >= b.BIG:
                    outputs.append(prepare_output(meta, t, j, cid, raw, body, tokens, "\n".join(context), messages,
                                                  replies, turn_texts, turn_calls, task))
            context += [b.clean(x) for x in b.message_texts(m)]
    # where each big output sits in each prompt (its first-shown content, unchanged)
    big = {o["tool_call_id"]: o for o in outputs}
    renderer = b.Renderer(tok)
    for t, row in enumerate(rows):
        req = row["request"]
        here = [(j, m) for j, m in enumerate(req["messages"]) if m.get("role") == "tool"
                and m.get("tool_call_id") in big and b.text_of(m.get("content")) == first[m.get("tool_call_id")]]
        if not here:
            continue
        full = c.full_text(tok, req)
        enc = tok(full, add_special_tokens=False, return_offsets_mapping=True)
        ends = renderer.bounds(req, full) if enc["input_ids"] == ids[t] else None
        if ends is None:
            raise RuntimeError(f"{meta['run_id']} call {t}: prompt not attributable")
        offsets = [o[0] for o in enc["offset_mapping"]]
        starts = [0] + ends[:-1]
        for j, m in here:
            raw = first[m["tool_call_id"]]
            at = full.find(raw, starts[j], ends[j])
            if at < 0:                   # the template trims a tool result's surrounding whitespace
                raw = raw.strip()
                at = full.find(raw, starts[j], ends[j])
            if at < 0:
                raise RuntimeError(f"{meta['run_id']} call {t}: output text not found in its message")
            a, z = bisect.bisect_left(offsets, at), bisect.bisect_left(offsets, at + len(raw))
            big[m["tool_call_id"]]["occurrences"].append([t, a, z - a])
    return {"calls": calls, "outputs": outputs}


def prepare_output(meta, t, j, cid, raw, body, tokens, context, messages, replies, turn_texts, turn_calls, task):
    producer = next((x for x in range(t - 1, -1, -1) for q in turn_calls[x] if q.get("id") == cid), None)
    call = next((q for q in turn_calls[producer] if q.get("id") == cid), {}) if producer is not None else {}
    args = call_args(call)
    source = b.paths(" ".join(str(v) for v in args.values()))
    if isinstance(args.get("path"), str):
        source.add(args["path"].rstrip("/"))
    lines = body.split("\n")
    spans = chunk_lines(body)
    chunks = ["\n".join(lines[a - 1:z]) for a, z in spans]
    chunk_tokens = [count(x) for x in chunks]
    # later use (12c's rules, at this chunk size)
    later_text = "\n".join(x for texts in turn_texts[t:] for x in texts)
    later_grams = {later_text[i:i + b.QUOTE] for i in range(len(later_text) - b.QUOTE + 1)}
    old_grams, old_idents = b.grams(context), set(c.IDENT.findall(context))
    later_words = set(c.WORD.findall("\n".join(a for x in range(t, len(replies)) for a in b.arg_strings(replies[x]))))
    asked = []
    for x in range(t, len(replies)):
        for q in turn_calls[x]:
            a = call_args(q)
            name = (q.get("function") or {}).get("name")
            if name == "read_file" and isinstance(a.get("path"), str) and a["path"].rstrip("/") in source:
                s = int(a.get("start_line") or 1)
                asked.append((s, int(a.get("end_line") or s + 399)))
            command = a.get("command") if isinstance(a.get("command"), str) else ""
            if command and any(p in command for p in source):
                asked += c.ranges_in(command)
    used = []
    for x in chunks:
        quoted = bool(b.grams(x) - old_grams & later_grams)
        numbers = {int(m.group(1)) for ln in x.split("\n") if (m := c.NUMBERED.match(ln))}
        by_line = any(a <= n <= z for n in numbers for a, z in asked)
        idents = {w for w in c.IDENT.findall(x) if w not in old_idents and
                  ("_" in w or any(ch.isdigit() for ch in w) or any(ch.isupper() for ch in w[1:]) or len(w) >= 8)}
        used.append(quoted or by_line or bool(idents & later_words))
    # BM25 queries
    before = messages[:j]
    last_text = next((b.text_of(m.get("content")) for m in reversed(before)
                      if m.get("role") == "assistant" and b.text_of(m.get("content")).strip()), "")
    q_reply = last_text + "\n" + " ".join(str(v) for v in args.values())
    recent = [call_args(q) for m in before if m.get("role") == "assistant" for q in m.get("tool_calls") or []][-3:]
    q_task = task + "\n" + "\n".join(" ".join(str(v) for v in a.values()) for a in recent)
    docs = [[w.lower() for w in c.WORD.findall(x)] for x in chunks]

    def rank(query):
        scores = c.bm25([w.lower() for w in c.WORD.findall(query)], docs)
        return sorted(range(len(chunks)), key=lambda i: (-scores[i], i))
    n = len(chunks)
    head_tail = []
    for i in range((n + 1) // 2):
        head_tail += [i] + ([n - 1 - i] if n - 1 - i != i else [])
    return {"run_id": meta["run_id"], "label": meta["label"], "arm": meta["arm"], "t": t, "message": j,
            "tool_call_id": cid, "tool": (call.get("function") or {}).get("name"), "args": args,
            "tokens": tokens, "spans": spans, "chunk_tokens": chunk_tokens, "used": used,
            "rank": {"head_tail": head_tail, "bm25_reply": rank(q_reply), "bm25_task": rank(q_task),
                     "oracle": [i for i in range(n) if used[i]]},
            "last_text_found": bool(last_text.strip()), "occurrences": []}


def prepare(workers: int) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(workers) as pool:
        results = list(pool.map(prepare_run, b.metas()))
    with (OUT / "outputs.jsonl").open("w") as f:
        for r in results:
            for o in r["outputs"]:
                f.write(json.dumps(o) + "\n")
    with (OUT / "calls.jsonl").open("w") as f:
        for r in results:
            for x in r["calls"]:
                f.write(json.dumps(x) + "\n")
    outs = [o for r in results for o in r["outputs"]]
    print(f"{len(results)} runs, {sum(len(r['calls']) for r in results)} calls, {len(outs)} big outputs, "
          f"{sum(len(o['spans']) for o in outs)} chunks ({sum(sum(o['used']) for o in outs)} later used), "
          f"{sum(len(o['occurrences']) for o in outs)} output occurrences in prompts")


# jev ---------------------------------------------------------------------------------------------------------------
def env_value(name: str) -> str | None:
    for line in (b.ROOT / ".env").read_text().splitlines():
        key, _, value = line.partition("=")
        if key.strip() == name and value.strip():
            return value.strip().strip('"').strip("'")
    return None


def jev(pause: float, limit: int | None = None) -> None:
    import yaml
    from econocontext.predictor.jev import Jev
    cfg = yaml.safe_load((b.ROOT / "config" / "v2.yaml").read_text())["jev"]
    if "TYPESAFE_API_KEY" not in os.environ and env_value("TYPESAFE_API_KEY"):
        os.environ["TYPESAFE_API_KEY"] = env_value("TYPESAFE_API_KEY")
    predictor = Jev(cfg["model"], cfg["timeout_seconds"], cfg["max_input_tokens"])
    cache = OUT / "jev.jsonl"
    done = {json.loads(x)["key"] for x in cache.read_text().splitlines()} if cache.exists() else set()
    outputs = [json.loads(x) for x in (OUT / "outputs.jsonl").read_text().splitlines()]
    logs = {m["run_id"]: m for m in b.metas()}
    rows_of = {}
    asked = failed = skipped = 0
    for o in outputs:
        key = f"{o['run_id']}|{o['tool_call_id']}"
        if key in done:
            continue
        if limit is not None and asked >= limit:
            break
        if len(o["spans"]) < 2:      # Jev's question needs two or more parts
            skipped += 1
            with cache.open("a") as f:
                f.write(json.dumps({"key": key, "status": "not asked (one chunk)"}) + "\n")
            continue
        if o["run_id"] not in rows_of:
            rows_of[o["run_id"]] = [r for r in map(json.loads, (b.ROOT / logs[o["run_id"]]["calls_log"]).read_text().splitlines())
                                    if r.get("call_no") and r.get("status") == 200 and isinstance(r.get("response"), dict)
                                    and "completion_tokens" in (r.get("usage") or {})]
        rows = rows_of[o["run_id"]]
        messages = rows[o["t"]]["request"]["messages"]
        task = b.text_of(rows[0]["request"]["messages"][1].get("content"))
        body = b.clean(b.text_of(messages[o["message"]].get("content")))
        item = {"tool": o["tool"], "input": o["args"], "text": body, "chunks": [tuple(s) for s in o["spans"]]}
        started = time.monotonic()
        try:
            answers, usage = predictor.arrival(task, turns_of(messages[:o["message"]], cfg["max_state_chars"]), item)
            row = {"key": key, "status": "ok", "relevant": answers.get("relevant"),
                   "needed_again": answers.get("needed_again"), "usage": usage}
        except Exception as exc:     # Jev's own error text holds no key and no content (jev.py)
            failed += 1
            row = {"key": key, "status": "failed", "error": str(exc)[:120]}
        row["seconds"] = round(time.monotonic() - started, 2)
        asked += 1
        with cache.open("a") as f:
            f.write(json.dumps(row) + "\n")
        print(f"{asked}: {row['status']} {row.get('error', '')} ({row['seconds']} s)", flush=True)
        time.sleep(pause)
    print(f"Jev requests {asked} (failed {failed}); not asked, one chunk: {skipped}; cached before: {len(done)}")


# evaluate ----------------------------------------------------------------------------------------------------------
def keep(order: list[int], tokens: list[int], budget: float) -> set[int]:
    kept, total = set(), 0
    for i in order:
        if total + tokens[i] <= budget:
            kept.add(i)
            total += tokens[i]
    return kept


def evaluate() -> None:
    const = load(MEASURED)
    ct, ca = const["C_token"], const["C_attn"]
    outputs = [json.loads(x) for x in (OUT / "outputs.jsonl").read_text().splitlines()]
    calls = [json.loads(x) for x in (OUT / "calls.jsonl").read_text().splitlines()]
    answers = {}
    for x in (OUT / "jev.jsonl").read_text().splitlines() if (OUT / "jev.jsonl").exists() else []:
        row = json.loads(x)
        answers[row["key"]] = row
    rows = []
    for o in outputs:
        a = answers.get(f"{o['run_id']}|{o['tool_call_id']}") or {}
        n = len(o["spans"])
        if a.get("status") == "ok" and a.get("relevant"):
            probs = {int(k): v for k, v in a["relevant"].items()}
            o["rank"]["jev"] = sorted(range(n), key=lambda i: (-probs.get(i, 0.0), i))
            o["jev_status"] = "ok"
        elif n < 2:
            o["rank"]["jev"] = [0]            # one chunk: Jev was not asked; it is kept when it fits
            o["jev_status"] = "one chunk"
        else:
            o["rank"]["jev"] = None
            o["jev_status"] = a.get("status", "missing")
        row = {"run_id": o["run_id"], "label": o["label"], "arm": o["arm"], "tool_call_id": o["tool_call_id"],
               "tokens": o["tokens"], "chunks": n, "used_chunks": sum(o["used"]), "jev_status": o["jev_status"]}
        kept_tokens = sum(t for t, u in zip(o["chunk_tokens"], o["used"]) if u)
        row["oracle_exact_saved"] = o["tokens"] - kept_tokens
        for s in SLICERS:
            for budget in BUDGETS:
                order = o["rank"][s]
                if order is None:
                    row[f"{s}_{budget}_saved"] = row[f"{s}_{budget}_used_kept"] = None
                    continue
                kept = keep(order, o["chunk_tokens"], budget / 100 * o["tokens"])
                row[f"{s}_{budget}_saved"] = o["tokens"] - sum(o["chunk_tokens"][i] for i in kept)
                row[f"{s}_{budget}_used_kept"] = sum(1 for i in kept if o["used"][i])
        rows.append(row)
    with (OUT / "slicers.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    # FLOPs ---------------------------------------------------------------------------------------------------
    by_run = defaultdict(list)
    for x in calls:
        by_run[x["run_id"]].append(x)
    occurs = defaultdict(list)            # (run, t) -> [(a, L, row)]
    for o, row in zip(outputs, rows):
        for t, a, L in o["occurrences"]:
            occurs[(o["run_id"], t)].append((a, L, row))

    def run_flops(run_id, dropped):
        """Eq. 9 over the run with each present big output shortened by dropped(row) tokens (scaled to its span)."""
        total = 0.0
        for x in by_run[run_id]:
            P0, R0 = x["P"], x["R"]
            P, R = P0, R0
            for a, L, row in occurs.get((run_id, x["t"]), []):     # positions and R0 are the original prompt's
                k = min(L, round(L * dropped(row) / row["tokens"])) if row["tokens"] else 0
                P -= k
                if R0 >= a + L:
                    R -= k
                elif R0 > a:
                    R -= round(k * (R0 - a) / L)
            assert 0 <= R <= P, (run_id, x["t"], P, R)
            total += sum(eq9(P, R, x["G"], ct, ca))
        return total

    base = {r: run_flops(r, lambda row: 0) for r in by_run}
    whole = {r: base[r] - run_flops(r, lambda row: row["tokens"]) for r in by_run}
    saved = {}
    for s in SLICERS + ("oracle_exact",):
        for budget in (BUDGETS if s != "oracle_exact" else (None,)):
            field = f"{s}_{budget}_saved" if budget else "oracle_exact_saved"
            saved[(s, budget)] = {r: base[r] - run_flops(r, lambda row, f=field: row[f] or 0) for r in by_run}
    summarise(rows, outputs, base, whole, saved, answers)


def summarise(rows, outputs, base, whole, saved, answers):
    arm_of = {r["run_id"]: (b.LABELS[r["label"]], r["arm"]) for r in rows}
    for x in b.metas():
        arm_of.setdefault(x["run_id"], (b.LABELS[x["label"]], x["arm"]))
    groups = sorted({v for v in arm_of.values()}, key=lambda g: (["step 9", "10d", "step 11"].index(g[0]), g[1]))
    pct = lambda a, z: f"{100 * a / z:.1f}%" if z else "–"   # noqa: E731
    jev_rows = [r for r in rows if r["jev_status"] in ("ok", "one chunk")]
    statuses = defaultdict(int)
    for a in answers.values():
        statuses[a["status"]] += 1
    out = ["# Step 12d tables", "",
           f"{len(rows)} big outputs, {sum(r['chunks'] for r in rows)} chunks of about {CHUNK_TOKENS} tokens "
           f"({sum(r['used_chunks'] for r in rows)} later used, in {sum(r['used_chunks'] > 0 for r in rows)} outputs). "
           f"Jev: {dict(statuses)}; Jev's slicer is scored on the {len(jev_rows)} outputs it answered or that have one chunk.",
           "",
           f"Jev requests to TypeSafe: {sum(1 for a in answers.values() if a['status'] in ('ok', 'failed'))}, failed "
           f"{statuses.get('failed', 0)}; input tokens {sum((a.get('usage') or {}).get('input_tokens', 0) for a in answers.values()):,}, "
           f"output tokens {sum((a.get('usage') or {}).get('output_tokens', 0) for a in answers.values()):,}; "
           f"seconds per request: median {sorted(a['seconds'] for a in answers.values() if 'seconds' in a)[len(answers) // 2] if answers else 0}."]

    def grid(title, subset):
        tok = sum(r["tokens"] for r in subset)
        used = sum(r["used_chunks"] for r in subset)
        out.append(f"\n## {title}\n\n{len(subset)} outputs, {tok:,} tokens, {used} later-used chunks. "
                   "Each cell: tokens saved (share of the outputs' tokens); recall (later-used chunks kept).\n")
        out.append("| slicer | " + " | ".join(f"keep ≤ {x}%" for x in BUDGETS) + " |\n|---|" + "---|" * len(BUDGETS))
        for s in SLICERS:
            cells = []
            for budget in BUDGETS:
                rs = [r for r in subset if r[f"{s}_{budget}_saved"] is not None]
                if not rs:
                    cells.append("–")
                    continue
                sv = sum(r[f"{s}_{budget}_saved"] for r in rs)
                u = sum(r["used_chunks"] for r in rs)
                k = sum(r[f"{s}_{budget}_used_kept"] for r in rs)
                cells.append(f"{pct(sv, sum(r['tokens'] for r in rs))}; recall {pct(k, u)}" + ("" if len(rs) == len(subset) else f" (n={len(rs)})"))
            out.append(f"| {s} | " + " | ".join(cells) + " |")
        sv = sum(r["oracle_exact_saved"] for r in subset)
        out.append(f"| oracle, no budget | {pct(sv, tok)}; recall 100% (all three columns) |")

    grid("Overall", rows)
    grid("Overall, on the outputs Jev was scored on", jev_rows)
    for g in groups:
        subset = [r for r in rows if arm_of[r["run_id"]] == g]
        if subset:
            grid(f"{g[0]}, {g[1]}", subset)

    out.append("\n## FLOPs\n\nThe big outputs' share of each arm's total FLOPs (Eq. 9, every call), and the FLOPs each "
               "slicer saves: the prefill when an output is first computed plus the attention in every later call. "
               "**Assumption: no extra calls from misses** (a dropped later-used chunk is never fetched again).\n")
    out.append("| step | arm | runs | PFLOPs | big outputs' share | " +
               " | ".join(f"{s} 25/50/75%" for s in SLICERS) + " | oracle, no budget |\n|---|---|---|---|---|" + "---|" * (len(SLICERS) + 1))
    for g in groups:
        runs = [r for r in base if arm_of[r] == g]
        F = sum(base[r] for r in runs)
        cells = [" / ".join(pct(sum(saved[(s, x)][r] for r in runs), F) for x in BUDGETS) for s in SLICERS]
        out.append(f"| {g[0]} | {g[1]} | {len(runs)} | {F / 1e15:.2f} | {pct(sum(whole[r] for r in runs), F)} | "
                   + " | ".join(cells) + f" | {pct(sum(saved[('oracle_exact', None)][r] for r in runs), F)} |")
    F = sum(base.values())
    out.append(f"| all | all | {len(base)} | {F / 1e15:.2f} | {pct(sum(whole.values()), F)} | " +
               " | ".join(" / ".join(pct(sum(saved[(s, x)].values()), F) for x in BUDGETS) for s in SLICERS) +
               f" | {pct(sum(saved[('oracle_exact', None)].values()), F)} |")
    (OUT / "summary.md").write_text("\n".join(out) + "\n")
    print("\n".join(out))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=("prepare", "jev", "evaluate"))
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--pause", type=float, default=1.0, help="seconds between Jev requests")
    ap.add_argument("--limit", type=int, help="ask Jev about at most this many outputs (a first check)")
    a = ap.parse_args()
    {"prepare": lambda: prepare(a.workers), "jev": lambda: jev(a.pause, a.limit), "evaluate": evaluate}[a.phase]()


if __name__ == "__main__":
    main()
