"""Step 16: what share of CLM's FLOPs is plausibly removable waste, and what kind? Offline, no model calls, no code
change. FIXED-TRAJECTORY ESTIMATES on the canonical track, priced on step 15's piece model (plan (a), as executed:
within 1.4% of the real prompt tokens and 0.8% of the canonical FLOPs).

Runs: every CLM run (TBLite tc9/tc10s/tc11v; BrowseComp-Plus bcp13p) and, for contrast, our harness without context
management: TBLite raw (tc9, no budget), raw-32k (tc10s) and base-32k (tc11v); BrowseComp-Plus base (bcp13p).
BrowseComp-Plus data stays in ~/econo/bcp; only numbers, ids and turn numbers are written.

1. Waterfall: each call's Eq. 9 FLOPs, split into parts that sum to the run's total:
   - generation, C_token*G, by the call's commands: task, context management (only commands on CLM's context file,
     /tmp/.live_ctx/), mixed (both; not credited to management), none (no command);
   - prefill, C_token*U, by the uncached piece each token belongs to: system and task (first call); new observations
     (search results, documents, command outputs, management outputs); re-prefill of retained text after an edit or
     rollback; reasoning made visible (always its own part); text written by an edit (summaries, notices: new text
     inserted before the appended tail); own turns and harness messages (the previous reply, nudges); cache rounding
     (retained text uncached on a call without an edit: the 16-token blocks and the last-token rule);
   - attention, C_attn terms: prefill attention and generation attention.
2. Waste, each priced as a counterfactual on the same piece model, F(run) - F(run without it):
   (a) repeated actions: the same normalized command, search query or document id run again, with a byte-identical
       result body (no new information), while the earlier result was visible, or after CLM removed it (an edit, a
       rollback, or a rollback that dropped it before it was shown). Re-runs whose result changed are reported, not
       counted. Price: the repeat's call and the pieces it produced (its turn, output and reasoning) removed.
   (b) work redone after a rollback: actions in the 5 calls from a rollback whose normalized form repeats a command
       of the dropped turns; priced the same way. Sensitivity only (not in the total): rephrased repeats, the same
       action type with a word-set Jaccard of 0.6 or more.
   (c) turns after sufficiency. BrowseComp-Plus: the first call whose prompt (outside system and task) contains the
       gold answer (both normalized as the judge does; matched locally, only call numbers are written); that call
       could answer, so the calls after it are removed. TBLite, passing runs only: the last call that issued a
       state-changing task command (anything but a known read-only command; conservative); the next call could
       submit, so the calls after that are removed.
   (d) runs that ended at the turn limit or the budget without an answer: BrowseComp-Plus no answer file; TBLite not
       submitted and failed, excluding crashes (a context-window overflow counts as the budget). Their whole FLOPs.
   (e) unreferenced observations: tool outputs no part of which is referenced by a later reply (reasoning, content
       or command: a verbatim 30-character quote, or a distinctive name: path, identifier, docid or proper name).
       Price: those outputs removed from every prompt (the calls kept). Also as a share of observation prefill, and,
       on BrowseComp-Plus, per document inside a search result.
   (f) outside the requested list, added because the data showed it: replies cut at the output cap with neither a
       command nor content (thinking only, which the gateway strips), except a run's last call; priced as (a).
   The overlap-free total removes the union of (a), (b), (c) and (e) at once (and, separately, with (f)); a run in (d)
   counts whole.
3. (a) and (b) are split into context-manager-preventable (the earlier result was removed by CLM itself) and model
   behaviour (the earlier result was still visible).

Use: PYTHONPATH=. .venv/bin/python runs/tier-a/scripts/step16_waste.py [--workers 5] [--only SUBSTRING]
Output: runs/tier-a/step16/{runs,cases}.csv
"""

import argparse
import csv
import json
import re
import shlex
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "benchmarks" / "bcp"))
import step12b_analysis as b  # noqa: E402
import step13b_forecast as f13  # noqa: E402
import step14b_rereads as r14  # noqa: E402
import step15_placement as s15  # noqa: E402

from measure.flops import eq9  # noqa: E402
from measure.models import MEASURED, load  # noqa: E402
from measure.tokens import tokenizer  # noqa: E402

OUT = b.ROOT / "runs" / "tier-a" / "step16"
CONTRAST = {"raw", "raw-32k", "base-32k", "base"}
SUBMIT = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
QUOTE = 30
REDO_WINDOW = 5
NEAR = 0.6              # word-set Jaccard of a rephrased repeat (sensitivity only)
READ_ONLY = {"cat", "head", "tail", "grep", "egrep", "fgrep", "rg", "ls", "find", "wc", "echo", "printf", "pwd", "stat",
             "file", "diff", "nl", "sort", "uniq", "cut", "tr", "awk", "sed", "jq", "which", "type", "cd", "true", "test",
             "[", "du", "df", "ps", "id", "whoami", "env", "date", "md5sum", "sha256sum", "sha1sum", "xxd", "od",
             "hexdump", "strings", "less", "more", "column", "basename", "dirname", "realpath", "readlink", "tree",
             "history", "uname", "hostname", "printenv", "getfacl", "lsattr", "cmp", "zcat", "base64", "sleep"}
READ_ONLY_GIT = {"status", "log", "diff", "show", "branch", "rev-parse", "ls-files", "blame"}
REDIRECT = re.compile(r"(?<![0-9&])>>?\s*(?!&|/dev/null)\S")


def base_of(x):
    return x[0] if isinstance(x[0], tuple) else x


def nth_of(x):
    return x[1] if isinstance(x[0], tuple) else 1


def price(contexts, n_of, G, ct, ca):
    """Per call (P, R, F) of a sequence of piece contexts, as executed (step 15's cache at piece granularity)."""
    seen, out = set(), []
    for t, K in enumerate(contexts):
        h, cum, R, matched, hashes = 0, 0, 0, True, []
        for x in K:
            h = hash((h, x))
            cum += n_of[x]
            hashes.append(h)
            if matched and h in seen:
                R = cum
            else:
                matched = False
        P = cum + s15.GEN
        R = min(s15.BLOCK * (R // s15.BLOCK), s15.BLOCK * ((P - 1) // s15.BLOCK))
        seen.update(hashes)
        out.append((P, R, sum(eq9(P, R, G[t], ct, ca))))
    return out


def norm_action(name: str, args: dict) -> tuple | None:
    """A normalized action, or None for management, submission and empty calls."""
    if name in ("bcp_search", "bcp_get_document"):
        v = str(args.get("query") or args.get("docid") or "")
        return ("search" if name == "bcp_search" else "doc", " ".join(v.lower().split()))
    if name == "submit":
        return None
    if name in ("read_file", "write_file"):
        return (name, json.dumps(args, sort_keys=True))
    command = str(args.get("command", ""))
    if not command.strip() or s15.r14.CTX_PATH in command or "LIVE_CTX" in command or SUBMIT in command:
        return None
    if "bcp_search" in command:
        return ("search", " ".join(command.split("bcp_search", 1)[1].strip().strip("'\"").lower().split()))
    if "bcp_get_document" in command:
        return ("doc", command.split("bcp_get_document", 1)[1].strip().strip("'\"").lower())
    return ("bash", " ".join(command.split()))


def obs_type(name: str, args: dict) -> str:
    command = str(args.get("command", ""))
    if name == "bcp_search" or "bcp_search" in command:
        return "search results"
    if name == "bcp_get_document" or "bcp_get_document" in command:
        return "documents"
    if r14.CTX_PATH in command or "LIVE_CTX" in command:
        return "management outputs"
    return "command outputs"


def state_changing(name: str, args: dict) -> bool:
    """Conservative: anything that is not clearly read-only changes state (management on CLM's file excluded)."""
    if name in ("write_file",):
        return True
    if name in ("read_file", "submit", "bcp_search", "bcp_get_document"):
        return False
    command = str(args.get("command", ""))
    if not command.strip() or r14.CTX_PATH in command or "LIVE_CTX" in command or SUBMIT in command:
        return False
    if REDIRECT.search(command) or re.search(r"\btee\b|\bsed\s+(?:-[a-zA-Z]*i|--in-place)|-delete\b|-exec\b", command):
        return True
    for part in r14.SPLIT_CMD.split(command):
        try:
            words = shlex.split(part)
        except ValueError:
            words = part.split()
        while words and (re.fullmatch(r"\w+=\S*", words[0]) or words[0] in ("sudo", "time", "timeout", "nohup")):
            words = words[1:] if not (words[0] == "timeout" and len(words) > 1) else words[2:]
        if not words:
            continue
        if words[0] == "git" and len(words) > 1 and words[1] in READ_ONLY_GIT:
            continue
        if words[0] not in READ_ONLY:
            return True
    return False


IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")


def distinctive(text: str, bench: str) -> set[str]:
    """Distinctive names: paths, code-like identifiers (an underscore, a digit or an inner capital), docids and
    multi-word proper names. Ordinary words are not names."""
    out = b.paths(text) | {w for w in IDENT.findall(text) if "_" in w.strip("_") or any(ch.isdigit() for ch in w)
                           or any(ch.isupper() for ch in w[1:])}
    if bench == "bcp":
        out |= set(f13.DOCID.findall(text)) | set(f13.NAME.findall(text))
    return out


def windows(text: str) -> set[str]:
    return {text[i:i + QUOTE] for i in range(0, max(len(text) - QUOTE + 1, 0)) if len(set(text[i:i + QUOTE])) >= 6}


def analyse(meta: dict) -> dict:
    tok = tokenizer(MEASURED)
    k = load(MEASURED)
    ct, ca = k["C_token"], k["C_attn"]
    bench, clm = meta["bench"], meta["arm"].startswith("clm")
    rows = r14.load_rows(Path(meta["calls_log"]) if Path(meta["calls_log"]).is_absolute() else b.ROOT / meta["calls_log"])
    if not rows:
        return {"run": {"run_id": meta["run_id"], "skipped": True}, "cases": []}
    n = len(rows)
    replies = [(r["response"].get("choices") or [{}])[0].get("message") or {} for r in rows]
    G = [r["usage"]["completion_tokens"] for r in rows]
    real_P = [r["usage"]["prompt_tokens"] for r in rows]
    pieces, contexts = s15.decompose(rows, replies, tok)
    n_of = dict(pieces.tokens)
    for K in contexts:
        for x in K:
            n_of.setdefault(x, pieces.tokens.get(x, 0))
    kind = lambda x: pieces.kind.get(x, pieces.kind.get(base_of(x), ""))   # noqa: E731
    events = {t for t in range(1, n) if contexts[t][:len(contexts[t - 1])] != contexts[t - 1]}
    rollbacks = {t for t in events if any("CONTEXT LIMIT HIT" in pieces.text.get(base_of(x), "")
                                          for x in contexts[t] if x not in set(contexts[t - 1]))}
    full = price(contexts, n_of, G, ct, ca)
    F_full = sum(x[2] for x in full)

    # the actions of each reply, their outputs (from the next request) and the pieces they produced -----------------
    actions, produced, out_type = [], [set() for _ in range(n)], {}
    for t, rep in enumerate(replies):
        calls = rep.get("tool_calls") or []
        outs = []
        if t + 1 < n:
            msgs = rows[t + 1]["request"]["messages"]
            last = max((j for j, m in enumerate(msgs) if m["role"] == "assistant"), default=-1)
            outs = [b.text_of(m.get("content")) for m in msgs[last + 1:] if m["role"] == "tool"]
        own = set()
        content = b.text_of(rep.get("content"))
        if content.strip():
            own |= {x for x in pieces.cover(content) if pieces.kind.get(x) != "glue"}
        for j, c in enumerate(calls):
            fn = c.get("function") or {}
            name, args = fn.get("name") or "", r14.args_of(c)
            raw_args = fn.get("arguments") if isinstance(fn.get("arguments"), str) else json.dumps(fn.get("arguments") or {})
            cmd = ("cmd", s15.sha(f"{name} {raw_args}".strip()))
            own.add(cmd)
            out = outs[j] if j < len(outs) else None
            if out is not None:
                key = ("out", s15.sha(out))
                own.add(key)
                out_type.setdefault(key, obs_type(name, args))
            actions.append({"t": t, "name": name, "args": args, "norm": norm_action(name, args), "cmd": cmd,
                            "out": out, "body": r14.body_of(out)[0] if out is not None else None,
                            "changes": state_changing(name, args)})
        if t + 1 < n:
            new = [x for x in contexts[t + 1] if x not in set(contexts[t])]
            produced[t] = {x for x in new if base_of(x) in own}
    for K in contexts:
        for x in K:
            if kind(x) == "reasoning":
                produced[base_of(x)[1]].add(x)

    def removal(calls: set[int], spec: dict) -> float:
        """F of the run without these calls and without these pieces (key -> first call it is removed from)."""
        Ks, Gs = [], []
        for u, K in enumerate(contexts):
            if u in calls:
                continue
            gone = {x for x in K if x in spec and u >= spec[x]}
            keep = []
            for x in K:
                if x in gone:
                    continue
                if kind(x) in ("wrapper", "glue"):
                    anchor = base_of(x)[1]
                    anchor = anchor if nth_of(x) == 1 else (anchor, nth_of(x))
                    if anchor in gone:
                        continue
                keep.append(x)
            Ks.append(keep)
            Gs.append(G[u])
        return sum(x[2] for x in price(Ks, n_of, Gs, ct, ca))

    def turn_spec(calls: set[int]) -> dict:
        spec = {}
        for t in calls:
            for x in produced[t]:
                spec[x] = min(spec.get(x, t + 1), t + 1)
        return spec

    # 1. waterfall -------------------------------------------------------------------------------------------------
    parts = dict.fromkeys(["gen: task", "gen: management", "gen: mixed", "gen: none", "prefill: system and task",
                           "prefill: search results", "prefill: documents", "prefill: command outputs",
                           "prefill: management outputs", "prefill: re-prefill after edits and rollbacks",
                           "prefill: reasoning made visible", "prefill: text written by edits",
                           "prefill: own turns and harness messages", "prefill: cache rounding",
                           "attention: prefill", "attention: generation"], 0.0)
    obs_tokens = 0
    for t, K in enumerate(contexts):
        P, R, _ = full[t]
        names = [((c.get("function") or {}).get("name") or "", r14.args_of(c)) for c in replies[t].get("tool_calls") or []]
        mgmt = [obs_type(nm, a) == "management outputs" for nm, a in names]
        cls = "none" if not names else "management" if all(mgmt) else "mixed" if any(mgmt) else "task"
        parts[f"gen: {cls}"] += ct * G[t]
        parts["attention: prefill"] += ca * 0.5 * (P * P - R * R)
        parts["attention: generation"] += ca * (G[t] * P + 0.5 * G[t] * G[t])
        prev = set(contexts[t - 1]) if t else set()
        tail_start = len(K)
        while tail_start > 0 and K[tail_start - 1] not in prev:
            tail_start -= 1
        cum = 0
        for i, x in enumerate(K):
            s, e = cum, cum + n_of[x]
            cum = e
            u = max(0, e - max(s, R))
            if not u:
                continue
            if t == 0:
                part = "system and task"
            elif kind(x) == "reasoning":
                part = "reasoning made visible"
            elif x in prev:
                part = "re-prefill after edits and rollbacks" if t in events else "cache rounding"
            elif kind(x) == "output":
                part = out_type.get(base_of(x), "command outputs")
                obs_tokens += u
            elif i < tail_start and t in events:
                part = "text written by edits"
            else:
                part = "own turns and harness messages"
            parts[f"prefill: {part}"] += ct * u
        parts["prefill: own turns and harness messages"] += ct * s15.GEN       # the generation prompt

    # 2a / 2b. repeated actions and redone work ---------------------------------------------------------------------
    cases = []
    present = lambda key, t: any(base_of(x) == key for x in contexts[t])   # noqa: E731
    by_norm = {}
    rep_calls = {"visible": set(), "removed by edit": set(), "removed by rollback": set()}
    for a in actions:
        if a["norm"] is None:
            continue
        earlier = by_norm.get(a["norm"])
        by_norm[a["norm"]] = a
        if earlier is None or earlier["out"] is None or a["body"] is None:
            continue
        key = ("out", s15.sha(earlier["out"]))
        t = a["t"]
        if present(key, t):
            status = "visible"
        else:
            shown = [u for u in range(earlier["t"] + 1, t + 1) if present(key, u)]
            # never shown: its turn was dropped by a rollback before the model saw it; else removed after its last showing
            status = "removed by rollback" if not shown or max(shown) + 1 in rollbacks else "removed by edit"
        identical = a["body"].strip() == (earlier["body"] or "").strip()
        cases.append({"run_id": meta["run_id"], "category": "a", "call": t, "earlier_call": earlier["t"],
                      "status": status, "identical": identical, "action": a["norm"][0]})
        if identical:
            rep_calls[status].add(t)
    redo_calls, near_calls = set(), set()
    jaccard = lambda x, y: len(set(x.split()) & set(y.split())) / max(1, len(set(x.split()) | set(y.split())))   # noqa: E731
    for e in sorted(rollbacks):
        before, after_e = {base_of(x) for x in contexts[e - 1]}, {base_of(x) for x in contexts[e]}
        dropped = {a["norm"] for a in actions if a["norm"] and a["t"] < e and a["cmd"] not in after_e
                   and (a["cmd"] in before or a["t"] == e - 1)}
        for a in actions:
            if not (e <= a["t"] < e + REDO_WINDOW and a["norm"]):
                continue
            if a["norm"] in dropped:
                redo_calls.add(a["t"])
                cases.append({"run_id": meta["run_id"], "category": "b", "call": a["t"], "earlier_call": e,
                              "status": "removed by rollback", "identical": "", "action": a["norm"][0]})
            elif any(d[0] == a["norm"][0] and jaccard(d[1], a["norm"][1]) >= NEAR for d in dropped):
                near_calls.add(a["t"])          # sensitivity only: a rephrased repeat (word-set Jaccard >= NEAR)
                cases.append({"run_id": meta["run_id"], "category": "b near", "call": a["t"], "earlier_call": e,
                              "status": "removed by rollback", "identical": "", "action": a["norm"][0]})

    # 2c. turns after sufficiency ---------------------------------------------------------------------------------
    suff = None
    if bench == "bcp":
        import judge
        answer = judge.normalize(ANSWERS.get(meta["task"], "")) if ANSWERS else ""
        if answer:
            for t, r in enumerate(rows):
                text = " ".join(b.text_of(m.get("content")) for m in r["request"]["messages"][2:])
                if f" {answer} " in f" {judge.normalize(text)} ":
                    suff = t
                    break
        after = set(range(suff + 1, n)) if suff is not None else set()
    else:
        writes = [a["t"] for a in actions if a["changes"]]
        if meta.get("reward") == 1.0 and writes:
            suff = writes[-1] + 1
        after = set(range(suff + 1, n)) if suff is not None else set()

    # 2d. runs that ended without an answer --------------------------------------------------------------------------
    submitted = any(SUBMIT in str(a["args"].get("command", "")) or a["name"] == "submit" for a in actions)
    if bench == "bcp":
        no_answer = not meta.get("answer_file")
    else:
        # a crash is neither the turn limit nor the budget; a context-window overflow is the unbudgeted arms' budget
        no_answer = not submitted and meta.get("reward") != 1.0 and (meta.get("overflow") or not meta.get("exception"))

    # 2e. unreferenced observations ----------------------------------------------------------------------------------
    first_at = {}
    for t, K in enumerate(contexts):
        for x in K:
            if kind(x) == "output" and out_type.get(base_of(x)) != "management outputs":
                first_at.setdefault(x, t)
    reply_text = []
    for rep in replies:
        reply_text.append("\n".join([rep.get("reasoning_content") or rep.get("reasoning") or "", b.text_of(rep.get("content"))]
                                    + [(c.get("function") or {}).get("arguments") or "" for c in rep.get("tool_calls") or []]))
    last_use = {}                                          # 30-character window -> the last reply that contains it
    for t in range(n):
        for w in windows(reply_text[t]):
            last_use[w] = t

    def referenced(text: str, t: int) -> bool:
        if any(last_use.get(w, -1) >= t for w in windows(text)):
            return True
        names = distinctive(text, bench)
        return any(nm in reply_text[u] for u in range(t, n) for nm in names)
    unref, unref_tokens, obs_all, snip_all, snip_unref = {}, 0, 0, 0, 0
    for x, t in first_at.items():
        text = pieces.text.get(base_of(x), "")
        body = r14.body_of(text)[0]
        obs_all += n_of[x]
        if not referenced(body, t):
            unref[x] = t
            unref_tokens += n_of[x]
        if bench == "bcp" and out_type.get(base_of(x)) == "search results":
            starts = [m.start() for m in f13.DOCID.finditer(body)] + [len(body)]
            for s, e in zip(starts, starts[1:]):
                seg = body[s:e]
                tokens = r14.count(seg)
                snip_all += tokens
                if not referenced(seg, t):
                    snip_unref += tokens

    # (f), outside the requested list: replies cut at the output cap with no command and no content (thinking only)
    cut = {t for t, r in enumerate(rows) if not replies[t].get("tool_calls") and not b.text_of(replies[t].get("content")).strip()
           and ((r["response"].get("choices") or [{}])[0].get("finish_reason") == "length") and t < n - 1}

    # prices ---------------------------------------------------------------------------------------------------------
    rep_all = set().union(*rep_calls.values())
    W = {
        "a_visible": F_full - removal(rep_calls["visible"], turn_spec(rep_calls["visible"])),
        "a_removed_edit": F_full - removal(rep_calls["removed by edit"], turn_spec(rep_calls["removed by edit"])),
        "a_removed_rollback": F_full - removal(rep_calls["removed by rollback"], turn_spec(rep_calls["removed by rollback"])),
        "a": F_full - removal(rep_all, turn_spec(rep_all)),
        "b": F_full - removal(redo_calls, turn_spec(redo_calls)),
        "c": F_full - removal(after, {}),
        "e": F_full - removal(set(), dict(unref)),
        "f": F_full - removal(cut, turn_spec(cut)),
        "b_near": F_full - removal(near_calls, turn_spec(near_calls)),
    }
    union_calls = rep_all | redo_calls | after
    spec = turn_spec(rep_all | redo_calls)
    for x, t in unref.items():
        spec[x] = min(spec.get(x, t), t)
    W["total"] = F_full if no_answer else F_full - removal(union_calls, spec)
    spec_f = dict(spec)
    for x, t in turn_spec(cut).items():
        spec_f[x] = min(spec_f.get(x, t), t)
    W["total_with_f"] = F_full if no_answer else F_full - removal(union_calls | cut, spec_f)
    W["d"] = F_full if no_answer else 0.0
    for kx in list(W):
        W[kx] = max(W[kx], 0.0)
    run = {"bench": bench, "label": meta["label"], "arm": meta["arm"], "run_id": meta["run_id"], "calls": n,
           "reward": meta.get("reward"), "submitted": submitted, "no_answer": no_answer,
           "real_P": sum(real_P), "model_P": sum(x[0] for x in full), "F": F_full,
           **{f"W_{kx}": v for kx, v in W.items()},
           "repeat_calls": len(rep_all), "repeat_visible": len(rep_calls["visible"]),
           "repeat_removed_edit": len(rep_calls["removed by edit"]),
           "repeat_removed_rollback": len(rep_calls["removed by rollback"]),
           "repeat_changed": sum(1 for c in cases if c["category"] == "a" and not c["identical"]),
           "redo_calls": len(redo_calls), "near_redo_calls": len(near_calls), "cut_calls": len(cut), "cut_G": sum(G[t] for t in cut), "rollbacks": len(rollbacks), "sufficiency_call": "" if suff is None else suff,
           "after_calls": len(after), "obs_tokens": obs_all, "obs_prefill_tokens": obs_tokens,
           "unref_tokens": unref_tokens, "unref_outputs": len(unref), "outputs": len(first_at),
           "snippet_tokens": snip_all, "snippet_unref_tokens": snip_unref,
           **{f"P_{kx}": v for kx, v in parts.items()}}
    return {"run": run, "cases": cases}


ANSWERS: dict = {}


def metas(only: str | None) -> list[dict]:
    out = []
    for m in r14.metas(only):
        if not (m["arm"].startswith("clm") or m["arm"] in CONTRAST):
            continue
        if m["bench"] == "tblite":
            s = next((json.loads(p.read_text()) for p in b.ROOT.glob(f"runs/{m['label']}/**/summary.json")
                      if json.loads(p.read_text()).get("run_id") == m["run_id"]), {})
            m["reward"], m["overflow"], m["exception"] = s.get("reward"), s.get("overflow"), s.get("exception")
        else:
            pilot = {json.loads(x)["run_id"]: json.loads(x) for x in
                     (b.ROOT / "runs/tier-a/step13a/pilot.jsonl").read_text().split("\n") if x}
            m["answer_file"] = pilot.get(m["run_id"], {}).get("answer_file")
        out.append(m)
    return out


def init() -> None:
    global ANSWERS
    import judge
    try:
        ANSWERS = judge.load("answers_decrypted.jsonl", "answer")
    except OSError:
        ANSWERS = {}


def write(name: str, rows: list[dict]) -> None:
    if rows:
        with (OUT / name).open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--only")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(a.workers, initializer=init) as pool:
        results = [r for r in pool.map(analyse, metas(a.only)) if not r["run"].get("skipped")]
    if a.only:
        for r in results:
            print(json.dumps({k: v for k, v in r["run"].items() if not k.startswith("P_")}))
        return
    write("runs.csv", [r["run"] for r in results])
    write("cases.csv", [c for r in results for c in r["cases"]])
    print(f"{len(results)} runs analysed")


if __name__ == "__main__":
    main()
