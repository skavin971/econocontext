"""Step 12b: analysis only, over the logged runs of tc9 (step 9), tc10s (10d) and tc11v (step 11), every arm.
No API calls, no behaviour change: it reads each run's calls.jsonl (the gateway log) and rebuilds every
prompt with measure/ (the same rebuild and validation as the FLOPs measure).

Per run it writes:
1. FLOPs split: prefill of new tokens (C_token·U), generated tokens (C_token·G) and attention (C_attn·...,
   itself split into prefill and decode). The new prefill tokens U = P - R are split by source:
   - block_tail: identical, at the same position, to an earlier prompt, but in the last partial 16-token
     cache block (recomputed only because the cache works in blocks);
   - re_prefill: a message the run sent before (tool results by call id, assistant turns by their tool
     call ids or text, other messages by text), recomputed because something before it changed (an edit);
   - otherwise by the message's source: task_system (the system prompt with the tools, and the original
     task), tool_outputs, own_turns (the model's earlier replies), other (the harness's other user
     messages - nudges, notices, summaries - and the template's generation prompt).
   Each token is assigned to its message by rendering the conversation's prefixes with the model's
   chat template (each prefix render must match the full prompt up to the closing of a tool-result group).
2. Overlap: for each tool output when it first enters the context, the share of its lines already
   visible in that request (line text, trailing spaces removed), and the tokens saved by showing only
   the new lines (exact counts). Two variants: every non-blank line, and only lines of 8 or more
   non-space characters counted as possibly visible.
3. Later use: for each output of 1,000 tokens or more as shown, whether a later model turn (reasoning,
   text or tool-call arguments, in this or any later call) quotes it (an exact substring of 30 or more
   characters that was not already in the context before the output; substrings with fewer than 5
   distinct characters ignored), names a path the producing call read (read_file's path, or a path in
   the command), or names a path that first appeared in the output.
4. Loops: runs of 2 or more consecutive agent calls with the identical tool call(s), and runs of 2 or more
   consecutive identical error outputs (non-zero exit code; by hash). The calls in them, the calls
   after the first, and the FLOPs of those extra calls; and how many of the calls were cut at the output
   cap (G at max_tokens), whose tool calls arrive with their arguments dropped.
5. Thinking against prompt size: reasoning tokens per agent call against P, and Pearson and Spearman
   correlations within each run (runs with 5 or more agent calls).

CLM's tool outputs end with a "[context: ~N/M tokens]" line that changes on every call; it is removed
before hashing and line matching.

Use: PYTHONPATH=. .venv/bin/python runs/tier-a/scripts/step12b_analysis.py [--workers 6]
Output: runs/tier-a/step12b/{runs,calls,outputs,loops}.csv and summary.json
"""

import argparse
import bisect
import csv
import glob
import hashlib
import json
import re
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from measure.cache_sim import cached_prefix  # noqa: E402
from measure.flops import eq9, prompt_ids, validate, vllm_messages  # noqa: E402
from measure.models import MEASURED, load  # noqa: E402
from measure.tokens import count_text, tokenizer  # noqa: E402

LABELS = {"tc9": "step 9", "tc10s": "10d", "tc11v": "step 11"}
OUT = ROOT / "runs" / "tier-a" / "step12b"
BIG = 1000
QUOTE = 30
CLM_FOOTER = re.compile(r"\n?\[context: ~\d+/\d+ tokens\]\s*$")
EXIT = re.compile(r"\(exit[ _]code[ =](\d+)")
PATH = re.compile(r"(?<![\w.:/-])(?:~|\.{1,2})?(?:/[\w.@+-]+)+/?|(?<![\w/.-])[\w-]+(?:/[\w.@+-]+)+")
SOURCES = ("block_tail", "re_prefill", "task_system", "tool_outputs", "own_turns", "other")


def text_of(content) -> str:
    return "" if not content else content if isinstance(content, str) else json.dumps(content)


def clean(text: str) -> str:
    return CLM_FOOTER.sub("", text)


def arg_strings(message: dict) -> list[str]:
    """The string values of a message's tool-call arguments (what the model wrote in its calls)."""
    out = []
    for call in message.get("tool_calls") or []:
        raw = (call.get("function") or {}).get("arguments")
        try:
            args = json.loads(raw) if isinstance(raw, str) and raw.strip() else raw or {}
        except ValueError:
            args = {"_raw": raw}
        out += [v for v in (args.values() if isinstance(args, dict) else []) if isinstance(v, str)]
    return out


def message_texts(message: dict) -> list[str]:
    return [text_of(message.get("content"))] + arg_strings(message)


def identity(message: dict) -> str:
    role = message.get("role")
    if role == "tool":
        key = f"tool:{message.get('tool_call_id')}"
    elif role == "assistant" and message.get("tool_calls"):
        key = "assistant:" + ",".join(c.get("id") or "" for c in message["tool_calls"])
    else:
        key = f"{role}:{hashlib.sha256(text_of(message.get('content')).encode()).hexdigest()}"
    return key


def paths(text: str) -> set[str]:
    return {p.rstrip("/.") for p in PATH.findall(text) if len(p.strip("/.~")) >= 3}


def grams(text: str) -> set[str]:
    return {text[i:i + QUOTE] for i in range(len(text) - QUOTE + 1) if len(set(text[i:i + QUOTE])) >= 5}


def exit_code(text: str) -> int | None:
    codes = EXIT.findall(text)
    return int(codes[-1]) if codes else (124 if "timed out" in text[-200:] else None)


def lcp(a: np.ndarray, b: np.ndarray) -> int:
    m = min(len(a), len(b))
    if m == 0:
        return 0
    diff = np.nonzero(a[:m] != b[:m])[0]
    return int(diff[0]) if len(diff) else m


def pearson(x, y):
    if len(x) < 5 or len(set(x)) < 2 or len(set(y)) < 2:
        return None
    return float(np.corrcoef(x, y)[0, 1])


def ranks(v):
    order = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2
        i = j + 1
    return r


CLOSE = len("<|im_end|>\n")


class Renderer:
    """Message boundaries in the rendered prompt, from prefix renders (cached across a run's calls). A prefix
    render is an exact prefix of the full prompt, except where the prefix ends inside a group of consecutive
    tool results: the template then closes the group early ("<|im_end|>"), so the boundary is where the two
    renders part, and nothing else may differ."""

    def __init__(self, tok):
        self.tok, self.cache = tok, {}

    def bounds(self, request: dict, full: str) -> list[int] | None:
        opts = dict(request.get("chat_template_kwargs") or {})
        if "reasoning_effort" in request:
            opts["reasoning_effort"] = request["reasoning_effort"]
        tools = request.get("tools") or None
        messages = vllm_messages(request["messages"])
        base = hashlib.sha256(json.dumps([tools, opts], sort_keys=True).encode()).hexdigest()
        ends, chain = [], base
        for k in range(len(messages)):
            chain = hashlib.sha256((chain + json.dumps(messages[k], sort_keys=True)).encode()).hexdigest()
            if k == 0:
                start = full.find("<|im_start|>", 1)
                ends.append(start if start > 0 else 0)
                continue
            if chain not in self.cache:
                self.cache[chain] = self.tok.apply_chat_template(messages[:k + 1], tools=tools,
                                                                 add_generation_prompt=False, tokenize=False, **opts)
            pre = self.cache[chain]
            n = len(pre) if full.startswith(pre) else next(i for i, (x, y) in enumerate(zip(pre, full)) if x != y)
            if len(pre) - n > CLOSE:    # only the closing of a group of tool results may differ
                return None
            ends.append(n)
        return ends


def analyse(meta: dict) -> dict:
    tok = tokenizer(MEASURED)
    const = load(MEASURED)
    ct, ca = const["C_token"], const["C_attn"]
    count = lambda text: count_text(text, MEASURED)   # noqa: E731
    rows = []
    for line in Path(ROOT / meta["calls_log"]).read_text().splitlines():
        row = json.loads(line)
        usage = row.get("usage") or {}
        if row.get("call_no") and row.get("status") == 200 and isinstance(row.get("response"), dict) \
                and "completion_tokens" in usage:
            rows.append(row)
    renderer = Renderer(tok)
    calls, prompts = [], []
    for row in rows:
        req = row["request"]
        ids = prompt_ids(tok, req)
        ok, _, _ = validate(tok, ids, row["response"].get("prompt_token_ids"))
        message = (row["response"].get("choices") or [{}])[0].get("message") or {}
        usage = row["usage"]
        calls.append({"call_no": row["call_no"], "kind": row.get("kind", "agent"), "ids": ids, "valid": ok,
                      "G": usage["completion_tokens"],
                      "think": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0,
                      "request": req, "reply": message})
        prompts.append(np.array(ids, dtype=np.int64))
    R = cached_prefix([c["ids"] for c in calls])
    task_text = text_of(calls[0]["request"]["messages"][1].get("content")) if calls else ""
    seen_ids: set[str] = set()
    call_rows, unattributed = [], 0
    for t, c in enumerate(calls):
        P, G = len(c["ids"]), c["G"]
        D = max([R[t]] + [lcp(prompts[t], prompts[s]) for s in range(t)])
        lin, attn = eq9(P, R[t], G, ct, ca)
        attn_prefill = ca * 0.5 * (P * P - R[t] * R[t])
        req = c["request"]
        messages = req["messages"]
        src = dict.fromkeys(SOURCES, 0)
        full = tok.apply_chat_template(vllm_messages(messages), tools=req.get("tools") or None, add_generation_prompt=True,
                                       tokenize=False, **{**(req.get("chat_template_kwargs") or {}),
                                                          **({"reasoning_effort": req["reasoning_effort"]}
                                                             if "reasoning_effort" in req else {})})
        enc = tok(full, add_special_tokens=False, return_offsets_mapping=True)
        ends = renderer.bounds(req, full) if enc["input_ids"] == c["ids"] else None
        if ends is None:
            unattributed += 1
        else:
            first_user = next((i for i, m in enumerate(messages) if m.get("role") == "user"), None)
            kinds = []
            for i, m in enumerate(messages):
                role = m.get("role")
                if identity(m) in seen_ids:
                    kinds.append("re_prefill")
                elif role == "system" or (i == first_user and text_of(m.get("content")) == task_text):
                    kinds.append("task_system")
                elif role == "tool":
                    kinds.append("tool_outputs")
                elif role == "assistant":
                    kinds.append("own_turns")
                else:
                    kinds.append("other")
            for pos in range(R[t], P):
                if pos < D:
                    src["block_tail"] += 1
                    continue
                k = bisect.bisect_right(ends, enc["offset_mapping"][pos][0])
                src[kinds[k] if k < len(kinds) else "other"] += 1
        seen_ids.update(identity(m) for m in messages)
        call_rows.append({"run_id": meta["run_id"], "call_no": c["call_no"], "kind": c["kind"], "valid": c["valid"],
                          "P": P, "R": R[t], "U": P - R[t], "G": G, "think": c["think"],
                          "F_prefill_new": ct * (P - R[t]), "F_generated": ct * G, "F_attn": attn,
                          "F_attn_prefill": attn_prefill, "F_attn_decode": attn - attn_prefill, "F": lin + attn,
                          **{f"U_{k}": v for k, v in src.items()}, "attributed": ends is not None})

    # Tool outputs, at their first appearance -------------------------------------------------------
    outputs, shown_at = [], {}
    for t, c in enumerate(calls):
        messages = c["request"]["messages"]
        context_lines: set[str] = set()
        context_text: list[str] = []
        for j, m in enumerate(messages):
            if m.get("role") == "tool" and m.get("tool_call_id") not in shown_at:
                shown_at[m.get("tool_call_id")] = t
                body = clean(text_of(m.get("content")))
                lines = body.split("\n")
                strip = [ln.rstrip() for ln in lines]
                nonblank = [ln for ln in strip if ln.strip()]
                vis_all = [ln for ln in nonblank if ln in context_lines]
                eligible = [ln for ln in nonblank if len(ln.replace(" ", "").replace("\t", "")) >= 8]
                vis_8 = [ln for ln in eligible if ln in context_lines]
                vis_set = {ln for ln in vis_8}
                kept = "\n".join(ln for ln in lines if ln.rstrip() not in vis_set or not ln.strip())
                tokens = count(body)
                producer = next((cc for cc in reversed(calls[:t]) for call in (cc["reply"].get("tool_calls") or [])
                                 if call.get("id") == m.get("tool_call_id")), None)
                call = next((call for call in (producer["reply"].get("tool_calls") or [])
                             if call.get("id") == m.get("tool_call_id")), None) if producer else None
                outputs.append({"run_id": meta["run_id"], "call_index": t, "tool_call_id": m.get("tool_call_id"),
                                "tool": (call or {}).get("function", {}).get("name"),
                                "args": (call or {}).get("function", {}).get("arguments"),
                                "tokens": tokens, "lines": len(nonblank), "visible_lines_all": len(vis_all),
                                "eligible_lines": len(eligible), "visible_lines_8": len(vis_8),
                                "saved_tokens": tokens - count(kept) if vis_set else 0,
                                "body": body, "context": "\n".join(context_text)})
            for text in message_texts(m):
                text = clean(text)
                context_text.append(text)
                context_lines.update(ln.rstrip() for ln in text.split("\n") if ln.strip())

    # Later use of big outputs ---------------------------------------------------------------------
    turn_texts = [[c["reply"].get("reasoning_content") or c["reply"].get("reasoning") or "",
                   text_of(c["reply"].get("content"))] + arg_strings(c["reply"]) for c in calls]
    for o in outputs:
        o.update(big=o["tokens"] >= BIG, quoted=None, source_path=None, new_path=None, used=None)
        if not o["big"]:
            continue
        later = "\n".join(x for texts in turn_texts[o["call_index"]:] for x in texts)
        new_grams = grams(o["body"]) - grams(o["context"])
        o["quoted"] = any(later[i:i + QUOTE] in new_grams for i in range(len(later) - QUOTE + 1))
        try:
            args = json.loads(o["args"]) if o["args"] else {}
        except ValueError:
            args = {}
        source = paths(" ".join(str(v) for v in args.values())) if isinstance(args, dict) else set()
        if isinstance(args, dict) and isinstance(args.get("path"), str):
            source.add(args["path"].rstrip("/"))
        o["source_path"] = any(p in later for p in source)
        fresh = paths(o["body"]) - paths(o["context"]) - source
        o["new_path"] = any(p in later for p in fresh)
        o["used"] = o["quoted"] or o["source_path"] or o["new_path"]

    # Loops ---------------------------------------------------------------------------------------------
    agent = [(t, c) for t, c in enumerate(calls) if c["kind"] != "summary"]
    output_of = {o["tool_call_id"]: o["body"] for o in outputs}
    seq = []
    for t, c in agent:
        tc = c["reply"].get("tool_calls") or []
        key = json.dumps([[x.get("function", {}).get("name"), x.get("function", {}).get("arguments")] for x in tc]) if tc else None
        outs = [output_of.get(x.get("id")) for x in tc]
        out = outs[0] if outs else None
        code = exit_code(out) if out else None
        err = hashlib.sha256(out.encode()).hexdigest() if out and code not in (None, 0) else None
        cap = c["request"].get("max_tokens")
        seq.append({"t": t, "key": key, "err": err, "F": call_rows[t]["F"], "cut": bool(cap) and c["G"] >= cap})
    loops = []
    for field, kind in (("key", "repeat_command"), ("err", "repeat_error")):
        i = 0
        while i < len(seq):
            j = i
            while j + 1 < len(seq) and seq[i][field] is not None and seq[j + 1][field] == seq[i][field]:
                j += 1
            if j > i:
                loops.append({"run_id": meta["run_id"], "kind": kind, "first_call": calls[seq[i]["t"]]["call_no"],
                              "length": j - i + 1, "extra_calls": j - i,
                              "cut_calls": sum(s["cut"] for s in seq[i:j + 1]), "calls": (seq[i]["key"] or "")[:160],
                              "F_extra": sum(s["F"] for s in seq[i + 1:j + 1]),
                              "extra_t": [s["t"] for s in seq[i + 1:j + 1]]})
            i = j + 1

    # Thinking against P ------------------------------------------------------------------------------
    xs = [call_rows[t]["P"] for t, _ in agent]
    ys = [call_rows[t]["think"] for t, _ in agent]
    r_p = pearson(xs, ys)
    r_s = pearson(ranks(xs), ranks(ys)) if r_p is not None else None

    tot = lambda k: sum(r[k] for r in call_rows)   # noqa: E731
    big = [o for o in outputs if o["big"]]
    extra = {t for lp in loops for t in lp["extra_t"]}
    run = {"label": meta["label"], "step": LABELS[meta["label"]], "run_id": meta["run_id"], "arm": meta["arm"],
           "task": meta["task"], "reward": meta.get("reward"), "calls": len(calls),
           "agent_calls": len(agent), "valid": sum(c["valid"] for c in calls), "unattributed_calls": unattributed,
           "P": tot("P"), "R": tot("R"), "U": tot("U"), "G": tot("G"), "think": tot("think"),
           "PF": tot("F") / 1e15, "PF_prefill_new": tot("F_prefill_new") / 1e15, "PF_generated": tot("F_generated") / 1e15,
           "PF_attn": tot("F_attn") / 1e15, "PF_attn_prefill": tot("F_attn_prefill") / 1e15,
           "PF_attn_decode": tot("F_attn_decode") / 1e15,
           **{f"U_{k}": tot(f"U_{k}") for k in SOURCES},
           "tool_outputs": len(outputs), "output_tokens": sum(o["tokens"] for o in outputs),
           "output_lines": sum(o["lines"] for o in outputs), "visible_lines_all": sum(o["visible_lines_all"] for o in outputs),
           "eligible_lines": sum(o["eligible_lines"] for o in outputs), "visible_lines_8": sum(o["visible_lines_8"] for o in outputs),
           "overlap_saved_tokens": sum(o["saved_tokens"] for o in outputs),
           "big_outputs": len(big), "big_tokens": sum(o["tokens"] for o in big),
           "big_unused": sum(not o["used"] for o in big), "big_unused_tokens": sum(o["tokens"] for o in big if not o["used"]),
           "big_unquoted": sum(not o["quoted"] for o in big),
           "loop_repeat_command_calls": sum(lp["length"] for lp in loops if lp["kind"] == "repeat_command"),
           "loop_repeat_error_calls": sum(lp["length"] for lp in loops if lp["kind"] == "repeat_error"),
           "loop_extra_calls": len(extra), "loop_extra_PF": sum(call_rows[t]["F"] for t in extra) / 1e15,
           "think_P_pearson": r_p, "think_P_spearman": r_s}
    for o in outputs:
        del o["body"], o["context"]
    for lp in loops:
        del lp["extra_t"]
    return {"run": run, "calls": call_rows, "outputs": outputs, "loops": loops}


def metas() -> list[dict]:
    found = []
    for label in LABELS:
        for path in sorted(glob.glob(str(ROOT / "runs" / label / "**" / "summary.json"), recursive=True)):
            s = json.loads(Path(path).read_text())
            if s.get("skipped") or not s.get("calls_log") or not (ROOT / s["calls_log"]).exists():
                continue
            found.append({"label": label, "run_id": s["run_id"], "arm": s.get("arm"), "task": s.get("task"),
                          "reward": s.get("reward"), "calls_log": s["calls_log"]})
    return found


def write(name: str, rows: list[dict]) -> None:
    if not rows:
        return
    with (OUT / name).open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--only", help="a run id substring, for a quick check")
    a = ap.parse_args()
    todo = [m for m in metas() if not a.only or a.only in m["run_id"]]
    OUT.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(a.workers) as pool:
        results = list(pool.map(analyse, todo))
    write("runs.csv", [r["run"] for r in results])
    write("calls.csv", [c for r in results for c in r["calls"]])
    write("outputs.csv", [o for r in results for o in r["outputs"]])
    write("loops.csv", [lp for r in results for lp in r["loops"]])
    print(f"{len(results)} runs, {sum(r['run']['calls'] for r in results)} calls "
          f"({sum(r['run']['valid'] for r in results)} valid, {sum(r['run']['unattributed_calls'] for r in results)} "
          f"not attributed), {sum(len(r['outputs']) for r in results)} tool outputs, "
          f"{sum(len(r['loops']) for r in results)} loops")


if __name__ == "__main__":
    main()
