"""Step 12c: analysis only, over the same 130 runs as 12b (tc9, tc10s, tc11v). No API calls, no behaviour change.

1. The model's own turns, as they are prefilled again in later prompts. Every token of an assistant message
   that is computed in a call (in U = P - R, beyond the block tail) is split by what it renders:
   - visible_text: the reply's text;
   - write_file_content: write_file's `content` argument;
   - bash_command: bash's `command` argument (of which bash_heredoc: the bodies of heredocs in it);
   - other_args: every other argument (paths, line numbers, other tools);
   - glue: the template around them (role header, empty think block, tool-call markup, parameter names).
   Each part's FLOPs: C_token per token, plus its share of prefill attention, C_attn·(position + ½) per
   token (these sum to the call's C_attn·½(P² − R²) over all its new tokens). Shares are of the arm's total
   FLOPs (Eq. 9, every call, generation included).
2. The free edit zone. For each call, the previous reply (the last assistant message) and the latest tool
   outputs (the tool messages after it) must lie in U, not in R: an edit there breaks no cached prefix.
   Checked on the whole message, and on its body (without the role header and the empty think block,
   which repeat the previous prompt's generation prompt). Exceptions are listed.
3. The slicing upper bound. Each output of 1,000+ tokens (as shown) is cut into 40-line chunks. A chunk is
   later used if a later model turn quotes it (an exact substring of 30+ characters that was not in the
   context before the output; substrings of fewer than 5 distinct characters ignored), or a later command
   (tool-call arguments) references it:
   - by line number: the chunk shows numbered lines ("N<tab>" or "N:" at the start of a line) and a later
     command on the same source path asks for one of them (read_file start_line-end_line; sed 'A,Bp' or
     'Ap'; head -n N; path:N);
   - by symbol: an identifier from the chunk that was not in the context before the output (4+ characters,
     with an underscore, a digit, an inner capital, or 8+ characters) appears as a word in a later command.
   The oracle slice keeps exactly the later-used chunks; its saving is the output's tokens minus theirs.
   BM25 slices rank the chunks against the agent's last visible text before the output (the latest
   non-empty reply text up to the producing reply; if there is none, the producing call's arguments)
   and keep the top chunks, in rank order, while they fit in 25%, 50% or 75% of the output's tokens.
   Reported: their savings and the later-used chunks each would have dropped.

Use: PYTHONPATH=. .venv/bin/python runs/tier-a/scripts/step12c_analysis.py [--workers 6]
Output: runs/tier-a/step12c/{own_turns,zone,slices}.csv
"""

import argparse
import bisect
import csv
import json
import math
import re
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import step12b_analysis as b  # noqa: E402

from measure.cache_sim import cached_prefix  # noqa: E402
from measure.flops import eq9, prompt_ids, vllm_messages  # noqa: E402
from measure.models import MEASURED, load  # noqa: E402
from measure.tokens import count_text, tokenizer  # noqa: E402

OUT = b.ROOT / "runs" / "tier-a" / "step12c"
PARTS = ("visible_text", "write_file_content", "bash_command", "bash_heredoc", "other_args", "glue")
CHUNK = 40
FRACTIONS = (0.25, 0.5, 0.75)
HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?[^\n]*\n(.*?)\n[ \t]*\1(?=\s|$)", re.S)
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")
WORD = re.compile(r"[A-Za-z0-9_]+")
NUMBERED = re.compile(r"^\s*(\d+)[\t:]")


def full_text(tok, req):
    opts = {**(req.get("chat_template_kwargs") or {}),
            **({"reasoning_effort": req["reasoning_effort"]} if "reasoning_effort" in req else {})}
    return tok.apply_chat_template(vllm_messages(req["messages"]), tools=req.get("tools") or None,
                                   add_generation_prompt=True, tokenize=False, **opts)


def assistant_ranges(full: str, start: int, end: int, message: dict) -> list[tuple[int, int, str]]:
    """Character ranges of the parts of one rendered assistant message (glue is what is left)."""
    ranges, cursor = [], start
    content = b.text_of(message.get("content"))
    if content:
        i = full.find(content, cursor, end)
        if i >= 0:
            ranges.append((i, i + len(content), "visible_text"))
            cursor = i + len(content)
    for call in message.get("tool_calls") or []:
        fn = call.get("function") or {}
        name, raw = fn.get("name"), fn.get("arguments")
        try:
            args = json.loads(raw) if isinstance(raw, str) and raw.strip() else raw or {}
        except ValueError:
            args = {}
        i = full.find(f"<function={name}>", cursor, end)
        if i < 0 or not isinstance(args, dict):
            continue
        cursor = i
        for key, value in args.items():
            marker = f"<parameter={key}>\n"
            j = full.find(marker, cursor, end)
            if j < 0:
                continue
            v0 = j + len(marker)
            text = value if isinstance(value, str) else json.dumps(value)
            if full[v0:v0 + len(text)] != text:
                continue
            v1 = v0 + len(text)
            part = "write_file_content" if (name == "write_file" and key == "content") else \
                "bash_command" if (name == "bash" and key == "command") else "other_args"
            if part == "bash_command":
                at = v0
                for m in HEREDOC.finditer(text):
                    h0, h1 = v0 + m.start(2), v0 + m.end(2)
                    if h0 > at:
                        ranges.append((at, h0, "bash_command"))
                    ranges.append((h0, h1, "bash_heredoc"))
                    at = h1
                if at < v1:
                    ranges.append((at, v1, "bash_command"))
            else:
                ranges.append((v0, v1, part))
            cursor = v1
    return sorted(ranges)


def body_start(full: str, start: int, end: int) -> int:
    """After the role header and the empty think block, which repeat the previous generation prompt."""
    i = full.find("</think>", start, end)
    if i >= 0:
        j = i + len("</think>")
        while j < end and full[j] == "\n":
            j += 1
        return j
    header = full.find("\n", start, end)
    return header + 1 if header >= 0 else start


def bm25(query: list[str], docs: list[list[str]], k1=1.5, bb=0.75) -> list[float]:
    n = len(docs)
    avg = sum(map(len, docs)) / n if n else 0
    df = Counter(w for d in docs for w in set(d))
    q = Counter(query)
    scores = []
    for d in docs:
        tf = Counter(d)
        s = 0.0
        for w in q:
            if w not in tf:
                continue
            idf = math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5))
            s += idf * tf[w] * (k1 + 1) / (tf[w] + k1 * (1 - bb + bb * len(d) / (avg or 1)))
        scores.append(s)
    return scores


def ranges_in(command: str) -> list[tuple[int, int]]:
    out = [(int(a), int(c)) for a, c in re.findall(r"(\d+)\s*,\s*(\d+)\s*p", command)]
    out += [(int(a), int(a)) for a in re.findall(r"(?<![\d,])(\d+)p\b", command)]
    out += [(1, int(a)) for a in re.findall(r"head\s+(?:-n\s*|-)(\d+)", command)]
    out += [(int(a), int(a)) for a in re.findall(r":(\d+)\b", command)]
    return out


def analyse(meta: dict) -> dict:
    tok = tokenizer(MEASURED)
    const = load(MEASURED)
    ct, ca = const["C_token"], const["C_attn"]
    count = lambda text: count_text(text, MEASURED)   # noqa: E731
    rows = []
    for line in (b.ROOT / meta["calls_log"]).read_text().splitlines():
        row = json.loads(line)
        usage = row.get("usage") or {}
        if row.get("call_no") and row.get("status") == 200 and isinstance(row.get("response"), dict) \
                and "completion_tokens" in usage:
            rows.append(row)
    renderer = b.Renderer(tok)
    ids_all = [prompt_ids(tok, r["request"]) for r in rows]
    prompts = [np.array(x, dtype=np.int64) for x in ids_all]
    R = cached_prefix(ids_all)
    replies = [(r["response"].get("choices") or [{}])[0].get("message") or {} for r in rows]
    parts = dict.fromkeys(PARTS, 0)
    parts_F = dict.fromkeys(PARTS, 0.0)
    F_total, zone = 0.0, []
    for t, row in enumerate(rows):
        req, ids = row["request"], ids_all[t]
        P, G = len(ids), row["usage"]["completion_tokens"]
        F_total += sum(eq9(P, R[t], G, ct, ca))
        D = max([R[t]] + [b.lcp(prompts[t], prompts[s]) for s in range(t)])
        full = full_text(tok, req)
        enc = tok(full, add_special_tokens=False, return_offsets_mapping=True)
        ends = renderer.bounds(req, full) if enc["input_ids"] == ids else None
        if ends is None:
            zone.append({"run_id": meta["run_id"], "call_no": row["call_no"], "kind": row.get("kind", "agent"),
                         "check": "unattributed"})
            continue
        offsets = [o[0] for o in enc["offset_mapping"]]
        messages = req["messages"]
        starts = [0] + ends[:-1]

        def first_token(char):          # the first token starting at or after this character
            return bisect.bisect_left(offsets, char)

        # 1. own turns
        for k, m in enumerate(messages):
            if m.get("role") != "assistant":
                continue
            s0, s1 = starts[k], ends[k]
            ranges = assistant_ranges(full, s0, s1, m)
            r_starts = [x[0] for x in ranges]
            for pos in range(max(D, first_token(s0)), min(P, first_token(s1))):
                c = offsets[pos]
                i = bisect.bisect_right(r_starts, c) - 1
                part = ranges[i][2] if i >= 0 and ranges[i][0] <= c < ranges[i][1] else "glue"
                parts[part] += 1
                parts_F[part] += ct + ca * (pos + 0.5)

        # 2. the free edit zone
        last = max((k for k, m in enumerate(messages) if m.get("role") == "assistant"), default=None)
        if last is None:
            zone.append({"run_id": meta["run_id"], "call_no": row["call_no"], "kind": row.get("kind", "agent"),
                         "check": "no previous reply"})
            continue
        checks = [("previous reply", last, body_start(full, starts[last], ends[last]))]
        checks += [("tool output", k, starts[k]) for k in range(last + 1, len(messages))
                   if messages[k].get("role") == "tool"]
        for what, k, body in checks:
            whole, inner = first_token(starts[k]), first_token(body)
            status = "in U" if whole >= R[t] else "body in U, header in R" if inner >= R[t] else "body in R"
            zone.append({"run_id": meta["run_id"], "call_no": row["call_no"], "kind": row.get("kind", "agent"),
                         "check": what, "message": k, "status": status, "R": R[t], "first_token": whole,
                         "body_first_token": inner})

    # 3. slices of big outputs ----------------------------------------------------------------------
    turn_texts = [[r.get("reasoning_content") or r.get("reasoning") or "", b.text_of(r.get("content"))] + b.arg_strings(r)
                  for r in replies]
    turn_args = [b.arg_strings(r) for r in replies]
    turn_calls = [r.get("tool_calls") or [] for r in replies]
    shown, slices = set(), []
    for t, row in enumerate(rows):
        messages = row["request"]["messages"]
        context = []
        for j, m in enumerate(messages):
            if m.get("role") == "tool" and m.get("tool_call_id") not in shown:
                shown.add(m.get("tool_call_id"))
                body = b.clean(b.text_of(m.get("content")))
                tokens = count(body)
                if tokens >= b.BIG:
                    slices.append(slice_one(meta, t, m, body, tokens, "\n".join(context), messages[:j], replies,
                                            turn_texts, turn_args, turn_calls, count))
            context += [b.clean(x) for x in b.message_texts(m)]
    F_parts = {f"F_{k}": v for k, v in parts_F.items()}
    return {"own": {"label": meta["label"], "step": b.LABELS[meta["label"]], "run_id": meta["run_id"],
                    "arm": meta["arm"], "F_total": F_total, **{f"U_{k}": v for k, v in parts.items()}, **F_parts},
            "zone": zone, "slices": slices}


def slice_one(meta, t, message, body, tokens, context, before, replies, turn_texts, turn_args, turn_calls, count):
    call_id = message.get("tool_call_id")
    producer = next((x for x in range(t - 1, -1, -1) for c in turn_calls[x] if c.get("id") == call_id), None)
    call = next((c for c in turn_calls[producer] if c.get("id") == call_id), {}) if producer is not None else {}
    try:
        args = json.loads(call.get("function", {}).get("arguments") or "{}")
    except ValueError:
        args = {}
    args = args if isinstance(args, dict) else {}
    source = b.paths(" ".join(str(v) for v in args.values()))
    if isinstance(args.get("path"), str):
        source.add(args["path"].rstrip("/"))
    lines = body.split("\n")
    chunks = ["\n".join(lines[i:i + CHUNK]) for i in range(0, len(lines), CHUNK)]
    chunk_tokens = [count(c) for c in chunks]
    later_text = "\n".join(x for texts in turn_texts[t:] for x in texts)
    later_cmds = [(replies[x], a) for x in range(t, len(replies)) for a in turn_args[x]]
    old_grams, old_idents = b.grams(context), set(IDENT.findall(context))
    later_words = set(WORD.findall("\n".join(a for _, a in later_cmds)))
    later_grams = {later_text[i:i + b.QUOTE] for i in range(len(later_text) - b.QUOTE + 1)}
    # line numbers asked for by later commands on the same source path
    asked: list[tuple[int, int]] = []
    for x in range(t, len(replies)):
        for c in turn_calls[x]:
            fn = c.get("function") or {}
            try:
                a = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                continue
            if not isinstance(a, dict):
                continue
            if fn.get("name") == "read_file" and isinstance(a.get("path"), str) and a["path"].rstrip("/") in source:
                s = int(a.get("start_line") or 1)
                asked.append((s, int(a.get("end_line") or s + 399)))
            command = a.get("command") if isinstance(a.get("command"), str) else ""
            if command and any(p in command for p in source):
                asked += ranges_in(command)
    used, how = [], []
    for c in chunks:
        quoted = bool(b.grams(c) - old_grams & later_grams)
        numbers = {int(m.group(1)) for ln in c.split("\n") if (m := NUMBERED.match(ln))}
        by_line = any(a <= n <= z for n in numbers for a, z in asked)
        idents = {w for w in IDENT.findall(c) if w not in old_idents and
                  ("_" in w or any(ch.isdigit() for ch in w) or any(ch.isupper() for ch in w[1:]) or len(w) >= 8)}
        by_symbol = bool(idents & later_words)
        used.append(quoted or by_line or by_symbol)
        how.append("q" * quoted + "l" * by_line + "s" * by_symbol)
    # BM25 query: the latest non-empty reply text up to the producing reply, else the producing call's arguments
    query_text, fallback = "", False
    for m in reversed(before):
        if m.get("role") == "assistant" and b.text_of(m.get("content")).strip():
            query_text = b.text_of(m.get("content"))
            break
    if not query_text:
        query_text, fallback = " ".join(str(v) for v in args.values()), True
    scores = bm25([w.lower() for w in WORD.findall(query_text)], [[w.lower() for w in WORD.findall(c)] for c in chunks])
    order = sorted(range(len(chunks)), key=lambda i: (-scores[i], i))
    row = {"run_id": meta["run_id"], "label": meta["label"], "arm": meta["arm"], "call_index": t, "tool_call_id": call_id,
           "tool": call.get("function", {}).get("name"), "tokens": tokens, "chunks": len(chunks),
           "used_chunks": sum(used), "used_how": " ".join(h or "-" for h in how),
           "oracle_kept_tokens": sum(k for k, u in zip(chunk_tokens, used) if u),
           "query_fallback": fallback, "query_empty": not query_text.strip()}
    row["oracle_saved"] = tokens - row["oracle_kept_tokens"]
    for f in FRACTIONS:
        kept, total = set(), 0
        for i in order:
            if total + chunk_tokens[i] > f * tokens:
                break
            kept.add(i)
            total += chunk_tokens[i]
        tag = int(f * 100)
        row[f"bm25_{tag}_kept_tokens"] = total
        row[f"bm25_{tag}_saved"] = tokens - total
        row[f"bm25_{tag}_missed"] = sum(1 for i, u in enumerate(used) if u and i not in kept)
    return row


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--only")
    a = ap.parse_args()
    todo = [m for m in b.metas() if not a.only or a.only in m["run_id"]]
    OUT.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(a.workers) as pool:
        results = list(pool.map(analyse, todo))
    b.OUT = OUT
    b.write("own_turns.csv", [r["own"] for r in results])
    b.write("zone.csv", [z for r in results for z in r["zone"] if "status" in z] or [{}])
    b.write("zone_skipped.csv", [z for r in results for z in r["zone"] if "status" not in z])
    b.write("slices.csv", [s for r in results for s in r["slices"]])
    zone = [z for r in results for z in r["zone"] if "status" in z]
    print(f"{len(results)} runs; zone checks {len(zone)}: {dict(Counter(z['status'] for z in zone))}; "
          f"skipped {dict(Counter(z['check'] for r in results for z in r['zone'] if 'status' not in z))}; "
          f"big outputs {sum(len(r['slices']) for r in results)}")


if __name__ == "__main__":
    main()
