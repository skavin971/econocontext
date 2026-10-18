"""Step 14b: re-reads, versions and CLM's management cost. Offline, no model calls. FIXED-TRAJECTORY ESTIMATES: every
number replays logged trajectories; none is live behaviour. Priced on the canonical track (our stable rebuild of the
tools block, step 14a), with Eq. 9 and the cache simulator, on every logged request (summary calls included).

Data: TBLite (tc9, tc10s, tc11v: 130 runs) and the BrowseComp-Plus pilot (bcp13p: base 4, CLM 8; read from
~/econo/bcp, never written to the repo except as numbers and ids).

Units. An item is a tool output as first shown (its body: the displayed text without the exit-status line and CLM's
"[context: ...]" footer; the exit status is kept apart). Items of 200+ tokens are analysed; the mass below 200 is
reported. On BrowseComp-Plus each document inside a search result (its snippet) is also a unit, with its docid and rank,
and a get_document output is one unit.

Classes, against all earlier items of the run (the first that applies):
  E   exact body: byte-identical to an earlier body. A reference must restate the new event's source, rank, version
      and exit status. BrowseComp-Plus: incidental (a snippet returning inside a new search) or deliberate (a reopened
      document).
  N   near-exact: equal after normalizing volatile fields (rules: VOLATILE below). Discovery only, not savable; its
      mass and the cost of "reference + changed fields" are reported.
  V   revision: same source and projection as an earlier item, different body, and evidence of a revision of that
      source between the two reads (REVISION below). Different ranges or passages are projections, not revisions.
  R?  unknown revision: same source and projection, different body, no evidence. Not savable.
  P   partial: shares exact chunks with earlier items (content-defined chunks, about 64 tokens: CHUNKS below).
  U   new.
Sources: read_file -> path and range; bash reading one file (cat, head, tail, sed -n, nl) -> path and the command's
options (the projection); any other bash -> the normalized command string; BrowseComp-Plus -> the docid.

Stateful replay. For each logged request, in order, the messages are rewritten:
  - an E item's message becomes a reference when an earlier identical body is present as a body in the same request
    (after all of that request's own edits); otherwise it keeps its body, and if it was a reference before, that is a
    REPAIR (counted; its cost is priced by the cache simulator, since the restored body breaks the prefix there);
  - (ladder step 2) a V item's message becomes a unified diff against its visible earlier version, if the diff is
    under 30% of the body's tokens;
  - (ladder step 3) a P item's chunks that are present in earlier visible bodies become range pointers.
A candidate that appears in a request only inside some other text (CLM's rewritten context), or edited, cannot be
mapped: its tokens count as UNSUPPORTED there. Reference formats (FORMATS), diffs and pointers are tokenized for real;
each reference uses the format of median cost.

Potentially stale current-state evidence: for a V-class source, at a call where only reads from before its latest
revision are visible (no later read visible) and the call's own action touches that source (names its path).

CLM's management cost (upper bound): calls classified by their command as management-only (only commands on its
context file, /tmp/.live_ctx/), task-only, mixed or none; generation of management-only calls, and of the management
commands' own text in mixed calls (their thinking is not credited). Edits: a request that is not an append-only
extension of the previous one. The edit's re-prefill (tokens after the cached prefix) is split, message by message,
into new summary text (messages not in the previous request), the model's earlier reasoning made visible (text
CLM's context keeps but the gateway had stripped; matched against earlier replies' reasoning), new observations (the previous reply and its outputs,
prefilled in any case) and retained text whose cache reuse was lost (messages already in the previous request): only
the last is avoidable. Edit types: rollback (CLM's budget control dropping its newest turns, with its notice), summarize (new text of 50+
tokens), shorten (a message replaced by a shorter one
sharing its text), drop (messages removed), reorder, note (new text under 50 tokens), other.

Use: PYTHONPATH=. .venv/bin/python runs/tier-a/scripts/step14b_rereads.py [--workers 5] [--only SUBSTRING]
Output: runs/tier-a/step14b/{runs,items,stale,edits}.csv and summary.md
"""

import argparse
import csv
import difflib
import hashlib
import json
import re
import shlex
import statistics
import sys
import zlib
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import step12b_analysis as b  # noqa: E402
import step12c_analysis as c12  # noqa: E402

from measure.cache_sim import cached_prefix  # noqa: E402
from measure.flops import eq9, prompt_ids, vllm_messages  # noqa: E402
from measure.models import MEASURED, load  # noqa: E402
from measure.tokens import count_text, tokenizer  # noqa: E402

OUT = b.ROOT / "runs" / "tier-a" / "step14b"
BCP = Path.home() / "econo" / "bcp"
MIN_TOKENS = 200
CLM_FOOTER = re.compile(r"\n?\[context: ~\d+/\d+ tokens\]\s*$")
EXIT_LINE = re.compile(r"\n?\((?:exit code (\d+)[^)]*|exit_code=(\d+))\)\s*$")
CTX_PATH = "/tmp/.live_ctx"
READERS = {"cat", "head", "tail", "nl"}
VOLATILE = [   # (name, pattern): replaced by a placeholder before comparing (class N)
    ("iso datetime", r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?"),
    ("date", r"\b\d{4}-\d{2}-\d{2}\b|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* +\d{1,2}\b"),
    ("clock time", r"\b\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?\b"),
    ("duration", r"\b\d+(?:\.\d+)?\s?(?:ms|us|µs|s|sec|secs|seconds|min|mins|minutes|h|hrs)\b"),
    ("pid", r"\b(?:pid|PID)[ =:]*\d+\b"),
    ("memory address", r"\b0x[0-9a-fA-F]{4,}\b"),
    ("temp path", r"/tmp/[\w.\-]+|/var/folders/\S+"),
    ("progress counter", r"\b\d+(?:\.\d+)?%|\b\d+/\d+\b"),
]
WRITERS = re.compile(r"(>>?|\btee\b|\bsed\s+(?:-[a-zA-Z]*i|--in-place)|\bcp\b|\bmv\b|\brm\b|\btouch\b|\bpatch\b|\bchmod\b|"
                     r"\bchown\b|\bln\b|\bmkdir\b|\bunzip\b|\btar\b|\bpython3?\b.*open\(|\bperl\b.*-i)")
WORKSPACE = re.compile(r"\b(?:git\s+(?:checkout|apply|reset|stash|merge|pull|am|restore|commit|rebase)|make\b|mvn\b|"
                       r"gradle\b|npm\s+(?:install|run|ci)|pip3?\s+install|cargo\s+build|go\s+build|gcc\b|g\+\+|"
                       r"python3?\s+setup\.py|pytest\b|cmake\b)")
FORMATS = [
    "[unchanged: same output as entry #{ref} ({src}{rank}), version {ver}, exit {code}]",
    '<ref entry="{ref}" source="{src}"{rank} version="{ver}" exit="{code}"/>',
    "(identical to the output of call {ref}: `{src}`{rank}, version {ver}, exit status {code})",
]
SPLIT_CMD = re.compile(r"&&|\|\||;|\||\n")


def count(text: str) -> int:
    return count_text(text, MEASURED)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def normalize(text: str) -> str:
    for _, pattern in VOLATILE:
        text = re.sub(pattern, "<V>", text)
    return text


def chunks(text: str) -> list[tuple[int, int, str]]:
    """Content-defined chunks, about 64 tokens (~250 characters): a boundary after a line whose hash ends in two zero
    bits once the chunk has 120+ characters, or at 600 characters; long lines are cut by a rolling hash of a 32-character
    window. Returns (first line, last line, text)."""
    lines, out, cur, start = text.split("\n"), [], [], 0
    for i, line in enumerate(lines):
        pieces = [line]
        if len(line) > 600:
            pieces, at = [], 0
            for j in range(len(line)):
                if j - at >= 120 and (zlib.crc32(line[max(0, j - 32):j + 1].encode()) & 0xFF) == 0 or j - at >= 600:
                    pieces.append(line[at:j + 1])
                    at = j + 1
            pieces.append(line[at:])
        for piece in pieces:
            cur.append(piece)
            size = sum(len(x) + 1 for x in cur)
            if (size >= 120 and int(sha(piece)[:2], 16) % 4 == 0) or size >= 600:
                out.append((start + 1, i + 1, "\n".join(cur)))
                cur, start = [], i + 1
    if cur:
        out.append((start + 1, len(lines), "\n".join(cur)))
    return out


def body_of(text: str) -> tuple[str, str | None, str]:
    """(body, exit status, wrapper suffix) of a displayed tool output."""
    footer = CLM_FOOTER.search(text)
    rest = text[:footer.start()] if footer else text
    m = EXIT_LINE.search(rest)
    code = (m.group(1) or m.group(2)) if m else None
    body = rest[:m.start()] if m else rest
    return body, code, text[len(body):]


def args_of(call: dict) -> dict:
    try:
        a = json.loads((call.get("function") or {}).get("arguments") or "{}")
    except ValueError:
        return {}
    return a if isinstance(a, dict) else {}


def source_of(name: str, args: dict, bench: str) -> tuple[str, str, str | None]:
    """(source key with projection, display source, path or None)."""
    if name == "read_file":
        p = str(args.get("path", ""))
        return f"file:{p}:{args.get('start_line') or 1}-{args.get('end_line') or ''}", p, p
    if name in ("bcp_search", "bcp_get_document"):
        q = args.get("query") or args.get("docid") or ""
        return f"{name}:{q}", f"{name} {q}"[:80], None
    command = str(args.get("command", ""))
    parts = [x for x in SPLIT_CMD.split(command) if x.strip()]
    if len(parts) == 1:
        try:
            words = shlex.split(parts[0])
        except ValueError:
            words = parts[0].split()
        if words and (words[0] in READERS or (words[0] == "sed" and "-n" in words)):
            paths = [w for w in words[1:] if not w.startswith("-") and ("/" in w or "." in w) and not re.fullmatch(r"[\d,]+p?", w)]
            if len(paths) == 1:
                return f"file:{paths[0]}:{' '.join(w for w in words if w != paths[0])}", command[:80], paths[0]
    return "cmd:" + " ".join(command.split()), command[:80], None


def writes_of(name: str, args: dict) -> tuple[set[str], bool]:
    """(paths a call may have changed, whether it is a workspace-wide revision: a write, git or build step)."""
    if name == "write_file":
        return {str(args.get("path", ""))}, True
    command = str(args.get("command", ""))
    if not command:
        return set(), False
    touched = set()
    if WRITERS.search(command):
        touched |= {w.strip("'\";") for w in re.findall(r"[\w./-]*[/.][\w./-]+", command)}
    return touched, bool(touched) or bool(WORKSPACE.search(command))


def reference(ref: int, src: str, rank: str, ver: str, code: str | None) -> str:
    texts = [f.format(ref=ref, src=src, rank=rank, ver=ver, code=code if code is not None else "n/a") for f in FORMATS]
    costs = [count(t) for t in texts]
    return sorted(zip(costs, texts))[1][1]          # the median-cost format


def load_rows(path: Path) -> list[dict]:
    return [r for r in map(json.loads, [x for x in path.read_text().split("\n") if x])
            if r.get("call_no") and r.get("status") == 200 and isinstance(r.get("response"), dict)
            and "completion_tokens" in (r.get("usage") or {})]


def analyse(meta: dict) -> dict:
    bench = meta["bench"]
    tok = tokenizer(MEASURED)
    k = load(MEASURED)
    ct, ca = k["C_token"], k["C_attn"]
    rows = load_rows(Path(meta["calls_log"]) if Path(meta["calls_log"]).is_absolute() else b.ROOT / meta["calls_log"])
    if not rows:
        return {"run": {**{x: meta[x] for x in ("bench", "label", "arm", "run_id")}, "skipped": True}}
    replies = [(r["response"].get("choices") or [{}])[0].get("message") or {} for r in rows]
    G = [r["usage"]["completion_tokens"] for r in rows]
    think = [min(g, (r["usage"].get("completion_tokens_details") or {}).get("reasoning_tokens") or 0) for g, r in zip(G, rows)]
    calls_by_id, made_at = {}, {}
    for t, rep in enumerate(replies):
        for c in rep.get("tool_calls") or []:
            calls_by_id[c.get("id")] = c
            made_at[c.get("id")] = t
    # revision events: (call index, paths, workspace-wide)
    revisions = []
    for t, rep in enumerate(replies):
        for c in rep.get("tool_calls") or []:
            paths, wide = writes_of((c.get("function") or {}).get("name"), args_of(c))
            if paths or wide:
                revisions.append((t, paths, wide))

    # items, in order of first appearance -------------------------------------------------------------------------
    items, first_seen, small_mass, big_mass = [], {}, 0, 0
    for t, r in enumerate(rows):
        for m in r["request"]["messages"]:
            if m.get("role") != "tool":
                continue
            content = b.text_of(m.get("content"))
            if content in first_seen:
                continue
            first_seen[content] = len(items)
            body, code, wrapper = body_of(content)
            n = count(content)
            call = calls_by_id.get(m.get("tool_call_id"), {})
            name, args = (call.get("function") or {}).get("name") or "", args_of(call)
            if name == "bash" and "bcp_search" in str(args.get("command", "")):
                name, args = "bcp_search", {"query": str(args["command"]).split("bcp_search", 1)[1].strip(' "\'')}
            elif name == "bash" and "bcp_get_document" in str(args.get("command", "")):
                name, args = "bcp_get_document", {"docid": str(args["command"]).split("bcp_get_document", 1)[1].strip(' "\'')}
            key, src, path = source_of(name, args, bench)
            item = {"id": len(items), "t": t, "made": made_at.get(m.get("tool_call_id"), t - 1), "content": content,
                    "body": body, "code": code, "wrapper": wrapper, "tokens": n, "tool": name, "key": key, "src": src,
                    "path": path, "units": None}
            if bench == "bcp" and name == "bcp_search":
                try:
                    item["units"] = json.loads(body)
                except ValueError:
                    item["units"] = None
            items.append(item)
            if n >= MIN_TOKENS:
                big_mass += n
            else:
                small_mass += n
    # classify
    by_hash, by_norm, by_key = {}, {}, defaultdict(list)
    chunk_owner: dict[str, int] = {}
    unit_seen: dict[str, tuple[int, int]] = {}          # snippet hash -> (item id, rank)
    doc_seen: dict[str, int] = {}                        # get_document body hash -> item id
    for it in items:
        it["class"], it["ref"], it["p_share"] = "small" if it["tokens"] < MIN_TOKENS else "U", None, 0.0
        h, nh = sha(it["body"]), sha(normalize(it["body"]))
        it["chunks"] = chunks(it["body"])
        if it["tokens"] >= MIN_TOKENS:
            earlier_same = [j for j in by_key.get(it["key"], [])]
            if h in by_hash:
                it["class"], it["ref"] = "E", by_hash[h]
                if bench == "bcp":
                    it["e_kind"] = "deliberate" if it["tool"] == "bcp_get_document" else "incidental"
            elif nh in by_norm:
                it["class"], it["ref"] = "N", by_norm[nh]
            elif earlier_same:
                j = earlier_same[-1]
                revised = any(items[j]["made"] < rt <= it["made"] and (wide or (it["path"] and any(
                    p and (p == it["path"] or p.endswith("/" + it["path"].split("/")[-1])) for p in paths)))
                    for rt, paths, wide in revisions)
                it["class"], it["ref"] = ("V" if revised else "R?"), j
            else:
                shared = sum(count(c[2]) for c in it["chunks"] if sha(c[2]) in chunk_owner)
                if shared:
                    it["class"], it["p_share"] = "P", shared / max(1, it["tokens"])
        by_hash.setdefault(h, it["id"])
        by_norm.setdefault(nh, it["id"])
        by_key[it["key"]].append(it["id"])
        for c in it["chunks"]:
            chunk_owner.setdefault(sha(c[2]), it["id"])
        # BrowseComp-Plus units: snippets inside search results, and opened documents
        it["unit_classes"] = []
        if bench == "bcp":
            if it["units"]:
                for rank, u in enumerate(it["units"], 1):
                    uh = sha(u.get("snippet", ""))
                    it["unit_classes"].append(("E-incidental" if uh in unit_seen else "new", count(u.get("snippet", ""))))
                    unit_seen.setdefault(uh, (it["id"], rank))
            elif it["tool"] == "bcp_get_document":
                it["unit_classes"].append(("E-deliberate" if h in doc_seen else "new", it["tokens"]))
                doc_seen.setdefault(h, it["id"])

    # the stateful replay, as a ladder ------------------------------------------------------------------------------
    def transform(level: int):
        """level 0: as logged; 1: E references; 2: + V diffs; 3: + P chunk pointers. Returns (F, repairs, repaired
        tokens, unsupported tokens, candidate tokens, references, diffs, pointers)."""
        prompts, was_ref, repairs, repaired_tokens, unsupported, candidates = [], set(), 0, 0, 0, 0
        n_refs = n_diffs = n_ptrs = 0
        visible = {"E": 0, "V": 0, "P": 0}            # tokens whose earlier copy was visible when the item first arrived
        for t, r in enumerate(rows):
            msgs = [dict(m) for m in r["request"]["messages"]]
            contents = [b.text_of(m.get("content")) for m in msgs]
            present_items = {}
            for i, c in enumerate(contents):
                if msgs[i].get("role") == "tool" and c in first_seen:
                    present_items.setdefault(first_seen[c], i)
            whole = "\n".join(contents)
            if level:
                kept_bodies: dict[str, int] = {}
                kept_snippets: dict[str, int] = {}          # BrowseComp-Plus: snippet hash -> item showing it in full
                for iid in sorted(present_items):
                    it, i = items[iid], present_items[iid]
                    new = None
                    if it["units"] and it["class"] not in ("E",):   # repeated documents inside a search result
                        units, replaced, restored = [dict(u) for u in it["units"]], 0, 0
                        for rank, u in enumerate(units, 1):
                            uh = sha(u.get("snippet", ""))
                            j = kept_snippets.get(uh)
                            key = (iid, rank)
                            if j is not None and j != iid:
                                u["snippet"] = reference(j, f"docid {u.get('docid')}", f", rank {rank}", "v1", None)
                                replaced += count(it["units"][rank - 1].get("snippet", "")) - count(u["snippet"])
                                n_refs += 1
                                was_ref.add(key)
                            else:
                                if key in was_ref:
                                    repairs += 1
                                    repaired_tokens += count(u.get("snippet", ""))
                                    was_ref.discard(key)
                                kept_snippets.setdefault(uh, iid)
                        candidates += sum(count(u.get("snippet", "")) for u in it["units"]
                                          if sha(u.get("snippet", "")) in kept_snippets and kept_snippets[sha(u.get("snippet", ""))] != iid)
                        if replaced:
                            msgs[i]["content"] = json.dumps(units, indent=2, ensure_ascii=False) + it["wrapper"]
                            if it["t"] == t:
                                visible["E"] += replaced
                            if level < 3:
                                kept_bodies[it["body"]] = iid
                            continue
                    if it["class"] == "E" or (level >= 2 and it["class"] == "V") or (level >= 3 and it["class"] == "P"):
                        candidates += it["tokens"]
                    if it["class"] == "E":
                        ref = next((j for j in sorted(kept_bodies.values(), reverse=True)
                                    if items[j]["body"] == it["body"]), None)
                        if ref is not None:
                            version = sum(1 for j in by_key[it["key"]] if j <= iid)
                            new = reference(ref, it["src"], "", f"v{version}", it["code"]) + it["wrapper"]
                            n_refs += 1
                            was_ref.add(iid)
                        elif iid in was_ref:
                            repairs += 1
                            repaired_tokens += it["tokens"]
                            was_ref.discard(iid)
                    elif level >= 2 and it["class"] == "V" and it["ref"] in kept_bodies.values():
                        if it["t"] == t:
                            visible["V"] += it["tokens"]        # its earlier version is visible
                        old = items[it["ref"]]["body"]
                        diff = "\n".join(difflib.unified_diff(old.split("\n"), it["body"].split("\n"), lineterm="", n=0))
                        text = f"[diff against entry #{it['ref']} ({it['src']})]\n{diff}"
                        if count(text) < 0.3 * it["tokens"]:
                            new = text + it["wrapper"]
                            n_diffs += 1
                    elif level >= 3 and it["class"] == "P":
                        seen_chunks = {sha(c[2]): j for j in kept_bodies.values() for c in items[j]["chunks"]}
                        out_lines, any_ptr, run = [], False, None
                        for first, last, text in it["chunks"]:
                            j = seen_chunks.get(sha(text))
                            if j is not None and count(text) > 10:
                                ptr = f"[lines {first}-{last} as in entry #{j}]"
                                out_lines.append(ptr)
                                any_ptr = True
                                n_ptrs += 1
                            else:
                                out_lines.append(text)
                        if any_ptr:
                            new = "\n".join(out_lines) + it["wrapper"]
                    if new is not None:
                        msgs[i]["content"] = new
                        if it["t"] == t and it["class"] in ("E", "P"):
                            visible[it["class"]] += it["tokens"] if it["class"] != "P" else it["tokens"] - count(new)
                    else:
                        kept_bodies[it["body"]] = iid
                # candidates present only inside other text (CLM's rewritten context): not mappable
                for it in items:
                    if it["class"] in ("E", "V", "P") and it["id"] not in present_items and it["t"] <= t \
                            and len(it["body"]) > 200 and it["body"] in whole:
                        unsupported += it["tokens"]
                        candidates += it["tokens"]
            prompts.append(prompt_ids(tok, {**r["request"], "messages": msgs}))
        R = cached_prefix(prompts)
        F = sum(sum(eq9(len(p), R[i], G[i], ct, ca)) for i, p in enumerate(prompts))
        return F, repairs, repaired_tokens, unsupported, candidates, n_refs, n_diffs, n_ptrs, prompts, R, visible

    ladder = {lvl: transform(lvl) for lvl in (0, 1, 2, 3)}
    F0, prompts0, R0 = ladder[0][0], ladder[0][8], ladder[0][9]
    U0 = sum(len(p) - r for p, r in zip(prompts0, R0))

    # potentially stale current-state evidence ------------------------------------------------------------------------
    v_paths = {items[i]["path"] for i in range(len(items)) if items[i]["class"] == "V" and items[i]["path"]}
    stale_calls = stale_tokens = dangerous = broad_calls = broad_dangerous = 0
    stale_cases = []
    for t, r in enumerate(rows):
        contents = {b.text_of(m.get("content")) for m in r["request"]["messages"]}
        visible = [it for it in items if it["t"] <= t and it["content"] in contents and it["path"]]
        flagged = flagged_broad = False
        for path in {it["path"] for it in visible}:
            reads = [it for it in visible if it["path"] == path]
            newest = max(x["made"] for x in reads)
            later = [rt for rt, paths, wide in revisions if newest < rt < t and (
                path in paths or any(p.endswith("/" + path.split("/")[-1]) for p in paths))]
            if not later:
                continue
            action = " ".join(str(args_of(c)) for c in replies[t].get("tool_calls") or [])
            touches = path in action or path.split("/")[-1] in action
            is_v = path in v_paths
            flagged_broad = True
            broad_dangerous += touches
            if is_v:
                flagged = True
                stale_tokens += sum(x["tokens"] for x in reads)
                dangerous += touches
            if touches:
                stale_cases.append({"run_id": meta["run_id"], "call": t, "v_class": is_v,
                                    "path": path if bench == "tblite" else "", "read_at": newest, "revised_at": later[-1],
                                    "action": action[:300] if bench == "tblite" else ""})
        stale_calls += flagged
        broad_calls += flagged_broad

    # CLM's management cost --------------------------------------------------------------------------------------------
    mgmt = {"mgmt_calls": 0, "task_calls": 0, "mixed_calls": 0, "none_calls": 0, "F_gen_mgmt": 0.0, "F_gen_all": 0.0}
    edits = []
    if meta["arm"].startswith("clm"):
        for t, rep in enumerate(replies):
            commands = [str(args_of(c).get("command", "")) for c in rep.get("tool_calls") or []]
            parts = [p for cmd in commands for p in ([cmd] if "<<" in cmd else SPLIT_CMD.split(cmd)) if p.strip()]
            on_ctx = [p for p in parts if CTX_PATH in p or "LIVE_CTX" in p]
            P = len(prompts0[t])
            gen = ct * G[t] + ca * (G[t] * P + G[t] * G[t] / 2)
            mgmt["F_gen_all"] += gen
            if not commands:
                mgmt["none_calls"] += 1
            elif on_ctx and len(on_ctx) == len(parts):
                mgmt["mgmt_calls"] += 1
                mgmt["F_gen_mgmt"] += gen
            elif on_ctx:
                mgmt["mixed_calls"] += 1
                share = sum(count(p) for p in on_ctx)            # only the management commands' own text
                mgmt["F_gen_mgmt"] += ct * share + ca * share * (P + G[t] - think[t])
            else:
                mgmt["task_calls"] += 1
        for t in range(1, len(rows)):
            before = [b.text_of(m.get("content")) for m in rows[t - 1]["request"]["messages"]]
            after_msgs = rows[t]["request"]["messages"]
            after = [b.text_of(m.get("content")) for m in after_msgs]
            if after[:len(before)] == before:
                continue                                          # append-only: no edit
            ids = prompts0[t]
            # message boundaries, to place each message's tokens
            renderer = b.Renderer(tok)
            text = c12.full_text(tok, rows[t]["request"])
            enc = tok(text, add_special_tokens=False, return_offsets_mapping=True)
            ends = renderer.bounds(rows[t]["request"], text) if enc["input_ids"] == ids else None
            reply_prev = b.text_of(replies[t - 1].get("content"))
            prev_set = set(before)
            split = {"summary": 0, "reasoning_made_visible": 0, "observations": 0, "retained": 0}
            thoughts = [x.get("reasoning_content") or x.get("reasoning") or "" for x in replies[:t]]
            probes = {th[i:i + 120] for th in thoughts if len(th) >= 120 for i in range(0, len(th) - 120, 400)}
            F_lost = 0.0
            if ends is not None:
                import bisect
                offs = [o[0] for o in enc["offset_mapping"]]
                starts = [0] + ends[:-1]
                for j, c in enumerate(after):
                    a, z = bisect.bisect_left(offs, starts[j]), bisect.bisect_left(offs, ends[j])
                    s = max(a, R0[t])
                    if z <= s:
                        continue
                    n = z - s
                    if c in prev_set:
                        kind = "retained"
                        F_lost += ct * n + ca * ((s + z - 1) * n / 2 + n / 2)
                    elif after_msgs[j].get("role") == "tool" or c == reply_prev or j >= len(after) - 2:
                        kind = "observations"
                    elif any(pr in c for pr in probes):
                        kind = "reasoning_made_visible"     # the model's earlier thinking, now sent as text
                    else:
                        kind = "summary"
                    split[kind] += n
            new_text = [c for c in after if c not in prev_set]
            new_tokens = sum(count(c) for c in new_text)
            removed = [c for c in before if c not in set(after)]
            shortened = any(c and len(c) < len(o) and (c[:80] in o or o[:80] in c) for c in new_text for o in removed)
            if any("CONTEXT LIMIT HIT" in c or "ROLLED BACK" in c for c in new_text):
                etype = "rollback (budget control)"
            elif new_tokens >= 50 and not shortened:
                etype = "summarize"
            elif shortened:
                etype = "shorten"
            elif removed and not new_text:
                etype = "drop"
            elif sorted(after) == sorted(before):
                etype = "reorder"
            elif new_text and new_tokens < 50:
                etype = "note"
            else:
                etype = "other"
            edits.append({"run_id": meta["run_id"], "call": t, "type": etype, "new_tokens": new_tokens,
                          "removed_messages": len(removed), "mapped": ends is not None, **split, "F_lost": F_lost})

    run = {"bench": bench, "label": meta["label"], "arm": meta["arm"], "run_id": meta["run_id"], "calls": len(rows),
           "F": F0, "F_new_prefill": ct * U0, "small_mass": small_mass, "big_mass": big_mass,
           "has_edits": bool(edits) or meta["arm"].startswith(("clm", "econo", "summary")),
           **{f"F_level{lvl}": ladder[lvl][0] for lvl in (1, 2, 3)},
           "repairs": ladder[3][1], "repaired_tokens": ladder[3][2], "repairs_E": ladder[1][1],
           "unsupported_tokens": ladder[3][3], "candidate_tokens": ladder[3][4],
           "references": ladder[1][5], "diffs": ladder[2][6], "pointers": ladder[3][7],
           "visible_E": ladder[1][10]["E"], "visible_V": ladder[2][10]["V"], "visible_P": ladder[3][10]["P"],
           "stale_calls": stale_calls, "stale_tokens": stale_tokens, "dangerous_calls": dangerous,
           "stale_broad_calls": broad_calls, "dangerous_broad_calls": broad_dangerous,
           "agent_calls": sum(1 for r in rows if r.get("kind", "agent") != "summary"), **mgmt,
           "edits": len(edits), "F_lost": sum(e["F_lost"] for e in edits)}
    for cls in ("E", "N", "V", "R?", "P", "U", "small"):
        run[f"tok_{cls}"] = sum(it["tokens"] for it in items if it["class"] == cls)
    run["p_shared_tokens"] = sum(it["tokens"] * it["p_share"] for it in items if it["class"] == "P")
    for cls in ("E-incidental", "E-deliberate", "new"):
        run[f"unit_{cls}"] = sum(n for it in items for c, n in it["unit_classes"] if c == cls)
    # N: what "reference + changed fields" would cost
    run["N_ref_cost"] = sum(count(reference(items[it["ref"]]["id"], it["src"], "", "v?", it["code"])) +
                            sum(count(it["body"][j1:j2]) for op, i1, i2, j1, j2 in
                                difflib.SequenceMatcher(None, items[it["ref"]]["body"], it["body"], autojunk=False).get_opcodes()
                                if op != "equal") for it in items if it["class"] == "N")
    item_rows = [{"run_id": meta["run_id"], "id": it["id"], "call": it["t"], "tokens": it["tokens"], "class": it["class"],
                  "ref": it["ref"], "tool": it["tool"], "p_share": round(it["p_share"], 3),
                  "source_hash": sha(it["key"])[:12]} for it in items]
    return {"run": run, "items": item_rows, "stale": stale_cases, "edits": edits}


def metas(only: str | None) -> list[dict]:
    out = [{**m, "bench": "tblite"} for m in b.metas()]
    for path in sorted((BCP / "runs" / "bcp13p").glob("*/*/summary.json")):
        s = json.loads(path.read_text())
        if not s.get("skipped") and s.get("calls_log"):
            out.append({"bench": "bcp", "label": "bcp13p", "run_id": s["run_id"], "arm": s["arm"], "task": s["task"],
                        "calls_log": s["calls_log"]})
    return [m for m in out if not only or only in m["run_id"]]


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
    with ProcessPoolExecutor(a.workers) as pool:
        results = [r for r in pool.map(analyse, metas(a.only)) if not r["run"].get("skipped")]
    write("runs.csv", [r["run"] for r in results])
    write("items.csv", [i for r in results for i in r["items"]])
    write("stale.csv", [s for r in results for s in r["stale"]])
    write("edits.csv", [e for r in results for e in r["edits"]])
    print(f"{len(results)} runs analysed")


if __name__ == "__main__":
    main()
