"""Step 12f: an offline audit of information-gathering sequences, over the 130 runs of 12b-12d (tc9, tc10s, tc11v).
No API calls, no behaviour change.

Turns: the agent calls of a run (summary calls are not turns and do not break a sequence). A turn is
information gathering when every tool call in its reply is read-only:
- read_file;
- bash whose every part (split at &&, ||, ;, | and newlines) starts with cat, grep (egrep, fgrep), find
  (without -exec, -execdir, -delete or -ok), ls, sed -n, head, tail or cd; no output redirection except
  2>/dev/null and 2>&1, no heredoc, no $( ) or backticks.
A sequence is a maximal run of consecutive information-gathering turns. It ends at the first other turn
(a write, a test run, submit, any other command, or a reply without a tool call).

FLOPs of a turn (Eq. 9 terms, attributed to the tokens that cause them):
- generated: its call's generated tokens, thinking (reasoning_tokens, generated first) and visible (the
  rest), each C_token per token plus its decode attention, C_attn·(position + ½);
- admitted: its tool outputs' tokens wherever they are computed (in U) in later prompts, each C_token plus
  its prefill attention, C_attn·(position + ½) - normally once, in the next call;
- re-read: its reply's tokens wherever they are computed in later prompts, the same way.
A message's tokens include its share of the chat template (role header, tool-call markup). Not included,
and reported apart as "resident": the attention later tokens pay to these tokens while they sit in the
context (C_attn per later query token per resident token).

Labels, for each turn of a sequence after the first, from the previous turn's tool output:
- the command's targets: read_file's path; in bash, each non-option argument of each part: a path (it has
  a "/" or a file extension) or a symbol (an identifier of 4+ characters with an underscore, a digit, an
  inner capital, or 8+ characters; words of a grep pattern included); globs are skipped;
- a path matches if it, or its file name (4+ characters), appears verbatim in the previous output; a
  symbol matches if it appears there as a whole word;
- (b) choice among visible candidates: a path target matches one the previous output lists, and it lists 2 or
  more distinct ones (listed: a path or a file name with a known extension that opens a line, before any ":",
  as find and grep -n print it, or closes it, as ls -l prints it);
- (a) exact follow-up: otherwise, any target matches;
- (c) new: no target matches.

Short and long tasks: the 10 tasks ranked by the raw arm's mean agent calls in step 9 (tc9, 3 repeats each:
no context management, max_tokens 8,192), split at the median, 5 and 5.

Sensitivity: --rule owner uses EconoContext's own read-only rule (econocontext/commands.py read_only_bash: also
wc, echo, which, id, stat, diff, sort, jq, read-only git and more) instead of the list above.

Use: PYTHONPATH=. .venv/bin/python runs/tier-a/scripts/step12f_sequences.py [--workers 6] [--rule owner]
Output: runs/tier-a/step12f/{turns,sequences}.csv and summary.md
"""

import argparse
import bisect
import csv
import json
import os
import re
import shlex
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
from measure.tokens import tokenizer  # noqa: E402

OUT = b.ROOT / "runs" / "tier-a" / "step12f"
READ = {"cat", "grep", "egrep", "fgrep", "find", "ls", "sed", "head", "tail", "cd"}
SPLIT = re.compile(r"&&|\|\||;|\||\n")
EXTENSIONS = ("py|sh|txt|log|json|jsonl|csv|tsv|yaml|yml|xml|md|conf|cfg|ini|service|timer|java|js|ts|c|h|cpp|go|rs|"
              "toml|sql|html|jar|gz|tar|zip|so|env|lock|pom|properties|gradle|rb|pl|php|out|err|pid|key|pem|crt|db|"
              "sqlite|parquet|pkl|ipynb|r|R|dat|bin|list|rules|socket|d")
FILENAME = re.compile(rf"(?<![\w/.-])[\w.-]+\.(?:{EXTENSIONS})(?![\w/.-])")


def args_of(call: dict) -> dict:
    try:
        a = json.loads((call.get("function") or {}).get("arguments") or "{}")
    except ValueError:
        return {}
    return a if isinstance(a, dict) else {}


def words(part: str) -> list[str]:
    try:
        return shlex.split(part)
    except ValueError:
        return part.split()


RULE = "12f"     # or "owner": EconoContext's own rule (econocontext/commands.py read_only_bash), a sensitivity check


def read_only(call: dict) -> bool:
    name, a = (call.get("function") or {}).get("name"), args_of(call)
    if name == "read_file":
        return True
    if RULE == "owner":
        from econocontext.commands import read_only_bash
        return name == "bash" and bool((a.get("command") or "").strip()) and read_only_bash(a["command"])
    if name != "bash" or not isinstance(a.get("command"), str) or not a["command"].strip():
        return False
    command = a["command"].replace("2>/dev/null", " ").replace("2>&1", " ")
    if any(mark in command for mark in (">", "<<", "$(", "`")):
        return False
    for part in SPLIT.split(command):
        w = words(part.strip())
        if not w:
            continue
        if w[0] not in READ or (w[0] == "sed" and "-n" not in w):
            return False
        if w[0] == "find" and {"-exec", "-execdir", "-delete", "-ok"} & set(w):
            return False
    return True


def symbol(w: str) -> bool:
    return len(w) >= 4 and ("_" in w or any(ch.isdigit() for ch in w) or any(ch.isupper() for ch in w[1:]) or len(w) >= 8)


def targets(call: dict) -> tuple[set[str], set[str]]:
    """(paths, symbols) a read-only call asks about."""
    name, a = (call.get("function") or {}).get("name"), args_of(call)
    paths, syms = set(), set()
    if name == "read_file" and isinstance(a.get("path"), str):
        paths.add(a["path"].strip().rstrip("/"))
        return paths, syms
    for part in SPLIT.split(a.get("command") or ""):
        w = words(part.strip())
        for arg in w[1:]:
            if arg.startswith("-") or "*" in arg or "?" in arg or not arg.strip():
                continue
            if "/" in arg or FILENAME.fullmatch(arg):
                paths.add(arg.rstrip("/"))
            else:
                syms |= {x for x in c.IDENT.findall(arg) if symbol(x)}
    return {p for p in paths if p not in ("", ".", "..", "/")}, syms


def listed_names(text: str) -> set[str]:
    """File names and paths the output lists: the first token of a line (before any ":", as grep -n prints it)
    or its last token (as ls -l prints it), when it is a path or a file name with a known extension."""
    names = set()
    for line in text.split("\n"):
        words_ = line.split()
        if not words_:
            continue
        for w in {words_[0].split(":")[0], words_[-1]}:
            w = w.strip("'\",").rstrip("/")
            if ("/" in w and len(w) > 1) or FILENAME.fullmatch(w):
                names |= {w, os.path.basename(w)}
    return names


def label(call_targets: tuple[set[str], set[str]], previous: str) -> str:
    paths, syms = call_targets
    path_hit = False
    for p in paths:
        base = os.path.basename(p)
        if p in previous or (len(base) >= 4 and re.search(rf"(?<![\w.-]){re.escape(base)}(?![\w-])", previous)):
            path_hit = True
    sym_hit = any(re.search(rf"(?<!\w){re.escape(s)}(?!\w)", previous) for s in syms)
    listed = listed_names(previous)
    if path_hit and len(listed) >= 2 and any(p in listed or os.path.basename(p) in listed for p in paths):
        return "b"
    if path_hit or sym_hit:
        return "a"
    return "c"


def tri(s: int, z: int) -> float:
    """Sum of (position + 1/2) over positions s..z-1."""
    n = z - s
    return (s + z - 1) * n / 2 + n / 2 if n > 0 else 0.0


def analyse(meta: dict) -> dict:
    tok = tokenizer(MEASURED)
    k = load(MEASURED)
    ct, ca = k["C_token"], k["C_attn"]
    rows = [r for r in map(json.loads, (b.ROOT / meta["calls_log"]).read_text().splitlines())
            if r.get("call_no") and r.get("status") == 200 and isinstance(r.get("response"), dict)
            and "completion_tokens" in (r.get("usage") or {})]
    ids = [prompt_ids(tok, r["request"]) for r in rows]
    R = cached_prefix(ids)
    renderer = b.Renderer(tok)
    replies = [(r["response"].get("choices") or [{}])[0].get("message") or {} for r in rows]
    F_total = sum(sum(eq9(len(ids[t]), R[t], r["usage"]["completion_tokens"], ct, ca)) for t, r in enumerate(rows))
    computed = defaultdict(float)   # message identity -> FLOPs of its tokens wherever computed
    resident = defaultdict(float)   # message identity -> attention later tokens pay to it
    for t, row in enumerate(rows):
        req = row["request"]
        full = c.full_text(tok, req)
        enc = tok(full, add_special_tokens=False, return_offsets_mapping=True)
        ends = renderer.bounds(req, full) if enc["input_ids"] == ids[t] else None
        if ends is None:
            raise RuntimeError(f"{meta['run_id']} call {t}: prompt not attributable")
        offsets = [o[0] for o in enc["offset_mapping"]]
        starts = [0] + ends[:-1]
        P, G = len(ids[t]), row["usage"]["completion_tokens"]
        for j, m in enumerate(req["messages"]):
            if m.get("role") not in ("tool", "assistant"):
                continue
            key = b.identity(m)
            a, z = bisect.bisect_left(offsets, starts[j]), bisect.bisect_left(offsets, ends[j])
            s = max(a, R[t])
            if z > s:
                computed[key] += ct * (z - s) + ca * tri(s, z)
            resident[key] += ca * (z - a) * ((P - max(R[t], z)) + G)
    # turns
    turns = []
    for t, row in enumerate(rows):
        if row.get("kind", "agent") == "summary":
            continue
        reply, usage = replies[t], row["usage"]
        P, G = len(ids[t]), usage["completion_tokens"]
        T = min(G, (usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0)
        V = G - T
        calls = reply.get("tool_calls") or []
        reply_key = "assistant:" + ",".join(x.get("id") or "" for x in calls) if calls else None
        out_keys = [f"tool:{x.get('id')}" for x in calls]
        outputs = []
        for x in calls:
            text = next((b.clean(b.text_of(m.get("content"))) for later in rows[t + 1:]
                         for m in later["request"]["messages"]
                         if m.get("role") == "tool" and m.get("tool_call_id") == x.get("id")), "")
            outputs.append(text)
        turns.append({
            "run_id": meta["run_id"], "call_no": row["call_no"], "t": t,
            "read_only": bool(calls) and all(read_only(x) for x in calls),
            "tools": " ".join((x.get("function") or {}).get("name") or "?" for x in calls),
            "command": json.dumps([args_of(x) for x in calls])[:200],
            "G": G, "think": T,
            "F_think": ct * T + ca * (T * P + T * T / 2),
            "F_visible": ct * V + ca * (V * (P + T) + V * V / 2),
            "F_admitted": sum(computed.get(k2, 0.0) for k2 in out_keys),
            "F_reread": computed.get(reply_key, 0.0) if reply_key else 0.0,
            "F_resident": sum(resident.get(k2, 0.0) for k2 in out_keys) + (resident.get(reply_key, 0.0) if reply_key else 0.0),
            "_targets": [targets(x) for x in calls], "_output": "\n".join(outputs)})
    # sequences and labels
    sequences, current = [], []
    for turn in turns + [None]:
        if turn is not None and turn["read_only"]:
            current.append(turn)
            continue
        if current:
            sequences.append(current)
        current = []
    for n, seq in enumerate(sequences):
        for i, turn in enumerate(seq):
            turn["sequence"], turn["position"], turn["sequence_length"] = n, i + 1, len(seq)
            if i:
                paths = set().union(*(x[0] for x in turn["_targets"]))
                syms = set().union(*(x[1] for x in turn["_targets"]))
                turn["label"] = label((paths, syms), seq[i - 1]["_output"])
    for turn in turns:
        turn["F_turn"] = turn["F_think"] + turn["F_visible"] + turn["F_admitted"] + turn["F_reread"]
        turn.setdefault("sequence", None)
        turn.setdefault("position", None)
        turn.setdefault("sequence_length", None)
        turn.setdefault("label", None)
        del turn["_targets"], turn["_output"]
    seq_rows = [{"run_id": meta["run_id"], "sequence": n, "first_call": seq[0]["call_no"], "turns": len(seq),
                 **{f: sum(x[f] for x in seq) for f in ("G", "think", "F_think", "F_visible", "F_admitted", "F_reread",
                                                         "F_resident", "F_turn")},
                 "share_of_run": sum(x["F_turn"] for x in seq) / F_total,
                 "labels": "".join(x["label"] or "-" for x in seq)} for n, seq in enumerate(sequences)]
    return {"run": {"run_id": meta["run_id"], "label": meta["label"], "arm": meta["arm"], "task": meta["task"],
                    "F_total": F_total, "agent_turns": len(turns)}, "turns": turns, "sequences": seq_rows}


def set_rule(rule: str) -> None:
    global RULE
    RULE = rule


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--rule", choices=("12f", "owner"), default="12f",
                    help="owner: EconoContext's read-only rule instead (a sensitivity check; output in step12f/owner_rule)")
    a = ap.parse_args()
    global OUT, RULE
    RULE = a.rule
    if a.rule == "owner":
        OUT = OUT / "owner_rule"
    OUT.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(a.workers, initializer=set_rule, initargs=(a.rule,)) as pool:
        results = list(pool.map(analyse, b.metas()))
    b.OUT = OUT
    b.write("runs.csv", [r["run"] for r in results])
    b.write("turns.csv", [t for r in results for t in r["turns"]])
    b.write("sequences.csv", [s for r in results for s in r["sequences"]])
    summarise(results)


def summarise(results: list[dict]) -> None:
    runs = [r["run"] for r in results]
    # short and long tasks: raw arm, step 9, mean agent calls
    calls = defaultdict(list)
    for r in runs:
        if r["label"] == "tc9" and r["arm"] == "raw":
            calls[r["task"]].append(r["agent_turns"])
    mean_calls = {t: statistics.mean(v) for t, v in calls.items()}
    ranked = sorted(mean_calls, key=mean_calls.get)
    short = set(ranked[:len(ranked) // 2])
    pct = lambda x, z: f"{100 * x / z:.1f}%" if z else "–"   # noqa: E731
    out = ["# Step 12f tables", "",
           "Short and long tasks (raw arm, step 9, mean agent calls over 3 repeats; split at the median):", "",
           "| task | mean calls | group |", "|---|---|---|"]
    out += [f"| {t} | {mean_calls[t]:.1f} | {'short' if t in short else 'long'} |" for t in ranked]
    arm_of = {r["run_id"]: (b.LABELS[r["label"]], r["arm"]) for r in runs}
    groups = sorted(set(arm_of.values()), key=lambda g: (["step 9", "10d", "step 11"].index(g[0]), g[1]))
    turns = [t for r in results for t in r["turns"]]
    seqs = [s for r in results for s in r["sequences"]]
    task_of = {r["run_id"]: r["task"] for r in runs}
    F_of = {r["run_id"]: r["F_total"] for r in runs}

    def block(title, keep):
        out.append(f"\n## {title}\n")
        out.append("| step | arm | runs | agent turns | read-only turns | sequences (2+ turns) | mean length (2+) | "
                   "FLOPs in sequences | in 2+ sequences | in (a) turns | in (b) turns | in (a)+(b) | labels a/b/c |")
        out.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for g in groups:
            ids = {r for r, v in arm_of.items() if v == g and keep(task_of[r])}
            if not ids:
                continue
            F = sum(F_of[r] for r in ids)
            ts = [t for t in turns if t["run_id"] in ids]
            ss = [s for s in seqs if s["run_id"] in ids]
            long_ = [s for s in ss if s["turns"] >= 2]
            lab = defaultdict(int)
            fl = defaultdict(float)
            for t in ts:
                if t["label"]:
                    lab[t["label"]] += 1
                    fl[t["label"]] += t["F_turn"]
            out.append(f"| {g[0]} | {g[1]} | {len(ids)} | {len(ts)} | {sum(t['read_only'] for t in ts)} | {len(long_)} | "
                       f"{statistics.mean(s['turns'] for s in long_):.1f} | " if long_ else
                       f"| {g[0]} | {g[1]} | {len(ids)} | {len(ts)} | {sum(t['read_only'] for t in ts)} | 0 | – | ")
            out[-1] += (f"{pct(sum(s['F_turn'] for s in ss), F)} | {pct(sum(s['F_turn'] for s in long_), F)} | "
                        f"{pct(fl['a'], F)} | {pct(fl['b'], F)} | {pct(fl['a'] + fl['b'], F)} | "
                        f"{lab['a']}/{lab['b']}/{lab['c']} |")

    block("All tasks (shares of the arm's total FLOPs)", lambda task: True)
    block("Short tasks", lambda task: task in short)
    block("Long tasks", lambda task: task not in short)

    out.append("\n## What a sequence's FLOPs are made of (all arms, sequences of 2+ turns)\n")
    long_ = [s for s in seqs if s["turns"] >= 2]
    tot = sum(s["F_turn"] for s in long_)
    out.append("| sequences | turns | generated: thinking | generated: visible | admitted outputs | re-read replies | "
               "resident (not in the share) |\n|---|---|---|---|---|---|---|")
    out.append(f"| {len(long_)} | {sum(s['turns'] for s in long_)} | {pct(sum(s['F_think'] for s in long_), tot)} | "
               f"{pct(sum(s['F_visible'] for s in long_), tot)} | {pct(sum(s['F_admitted'] for s in long_), tot)} | "
               f"{pct(sum(s['F_reread'] for s in long_), tot)} | {sum(s['F_resident'] for s in long_) / tot:.2f}× |")
    lengths = defaultdict(int)
    for s in seqs:
        lengths[min(s["turns"], 6)] += 1
    out.append("\nSequence lengths (all arms): " + ", ".join(f"{'6+' if n == 6 else n}: {lengths[n]}" for n in sorted(lengths)))
    (OUT / "summary.md").write_text("\n".join(out) + "\n")
    print("\n".join(out))


if __name__ == "__main__":
    main()
