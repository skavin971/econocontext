"""Phase 0, T4: trace audit over every Study 2 trial, ours and CLM (read-only; no model calls).

The reviewer's audit_admission.py was not available in this environment, so this module defines its own event
format (to_events) and computes the requested measures from it; a converter to that script's format can be added
once its input schema is known.

Event format, one list per trial, in order:
  {"step": n, "kind": "search" | "results" | "document" | "page" | "answer" | "other",
   "docids": [...],                     # search hits, or the opened document
   "returned": [chunk hash, ...],       # uniform chunks the tool returned
   "admitted": [chunk hash, ...],       # of those, the chunks that entered the main model's prompt
   "admitted_docs": [...]}              # docids of the admitted chunks
Uniform chunks, the same for every arm (CLM included) so hashes compare: each source text (a hit's 512-token
snippet, or a document's 8,192-token view, both from the same Lucene service) is cut with prepare.chunk_document
into 102-token (snippet) or 400-token (document) chunks by the Qwen3-0.6B tokenizer. The arms themselves cut with
the main model's tokenizer, so their chunks are mapped by character spans: an event returns a uniform chunk when it
returned at least half of that chunk's characters. chunk hash = sha256(docid NUL kind NUL chunk text).
- Our arms: tools.jsonl holds every payload. Unpaged search (M0, M1) returns whole snippets; paged search and every
  document view return rendered chunks "[document D chunk C chars a:b]" whose a:b are spans of the source text.
  Our harness appends each payload verbatim, so admitted == returned.
- CLM: each bcp_search / bcp_get_document command is re-run against the same service, giving what the tool
  returned; admitted = the longest prefix of each source text that appears in the tool message CLM sent to the
  model (CLM caps tool output at 60,000 characters; commands can be piped through head, jq and the like).

Cut reply: a main-model call that hit the output cap (finish_reason "length") and carried no valid tool call.
"""

import collections
import hashlib
import json
import re
import shlex
from functools import lru_cache
from pathlib import Path

from benchmarks.bcp.doc_labeling.prepare import chunk_document

from . import order_audit as oa
from . import trials as T

SNIPPET_CHUNK, DOCUMENT_CHUNK = 102, 400
HALF = 0.5


class Uniform:
    """Uniform Qwen3-0.6B chunks of snippets and document views, fetched once from the search service."""

    def __init__(self, corpus, tokenizer_id="Qwen/Qwen3-0.6B"):
        from transformers import AutoTokenizer
        self.corpus, self.tok = corpus, AutoTokenizer.from_pretrained(tokenizer_id)
        self.snippets, self.documents = {}, {}

    def count(self, text):
        return len(self.tok.encode(text, add_special_tokens=False))

    def search(self, query):
        hits = self.corpus.search(query)
        for h in hits:
            self.snippets.setdefault(str(h["docid"]), h.get("snippet") or "")
        return hits

    def document(self, docid):
        if docid not in self.documents:
            self.documents[docid] = self.corpus.document(docid) or ""
        return self.documents[docid]

    @lru_cache(maxsize=None)
    def chunks(self, docid, kind):
        text = self.snippets.get(docid, "") if kind == "snippet" else self.document(docid)
        size = SNIPPET_CHUNK if kind == "snippet" else DOCUMENT_CHUNK
        return tuple((c["start"], c["end"], c["text"]) for c in chunk_document(text, docid, self.count, size)) if text else ()

    def covered(self, docid, kind, spans):
        """Hashes of the uniform chunks at least half covered by these character spans of the source text."""
        out = []
        for start, end, text in self.chunks(docid, kind):
            got = sum(max(0, min(end, b) - max(start, a)) for a, b in spans)
            if end > start and got / (end - start) >= HALF:
                out.append(hashlib.sha256(f"{docid}\0{kind}\0{text}".encode()).hexdigest())
        return out


def _spans(text):
    """docid -> [(a, b)] from rendered chunks."""
    out = collections.defaultdict(list)
    for c in oa.parse_pages([{"text": text, "chunk_ids": []}], check=False).values():
        out[c["docid"]].append((c["start"], c["end"]))
    return out


def ours_events(trial, uni):
    events, last_query = [], None
    for row in T.rows(Path(trial["trial_dir"]) / "tools.jsonl"):
        tool, res, args = row.get("tool"), row.get("result"), row.get("arguments") or {}
        ev = {"step": row.get("step"), "kind": "other", "docids": [], "returned": [], "admitted": [], "admitted_docs": []}
        spans, kind = {}, None
        if tool == "search":
            last_query = args.get("query", "")
            hits = uni.search(last_query)  # deterministic; registers the snippets
            ev.update(kind="search", docids=[str(h["docid"]) for h in hits])
            kind = "snippet"
            if isinstance(res, list):
                spans = {str(h["docid"]): [(0, len(h.get("snippet") or ""))] for h in res if h.get("snippet")}
            elif isinstance(res, dict) and isinstance(res.get("text"), str):
                spans = _spans(res["text"])
        elif tool == "next_results" and isinstance(res, dict):
            ev.update(kind="results", docids=[str(d) for d in res.get("hits", [])])
            kind = "snippet"
            spans = _spans(res["text"]) if isinstance(res.get("text"), str) else {}
        elif tool in ("get_document", "next_page") and isinstance(res, dict):
            ev.update(kind="document" if tool == "get_document" else "page", docids=[str(res.get("docid"))])
            kind = "document"
            spans = _spans(res["text"]) if isinstance(res.get("text"), str) else {}
        elif tool == "answer":
            ev["kind"] = "answer"
        for docid, s in spans.items():
            hashes = uni.covered(docid, kind, s)
            ev["returned"] += hashes
            if hashes:
                ev["admitted_docs"].append(docid)
        ev["admitted"] = list(ev["returned"])
        events.append(ev)
    return events


def clm_command(arguments):
    """('search', query) | ('document', docid) | None for one CLM bash call."""
    try:
        command = (json.loads(arguments) if isinstance(arguments, str) else arguments).get("command", "")
    except (json.JSONDecodeError, AttributeError):
        return None
    command = str(command)
    m = re.search(r"bcp_get_document\s+['\"]?(\w+)", command)
    if m:
        return ("document", m.group(1))
    if "bcp_search" in command:
        rest = re.split(r"\||&&|;|>", command.split("bcp_search", 1)[1])[0]
        try:
            words = shlex.split(rest)
        except ValueError:
            return None
        return ("search", " ".join(words).strip()) if words else None
    return None


def _decoded(text):
    """Tool output as the model read it, plus the JSON-unescaped strings inside it where it parses."""
    text = text or ""
    extra = []
    start = min([i for i in (text.find("["), text.find("{")) if i >= 0] or [-1])
    if start >= 0:
        try:
            extra = list(_strings(json.loads(text[start:])))
        except json.JSONDecodeError:
            # truncated JSON: undo escapes in the raw text so a prefix still matches
            extra = [text.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")]
    return "\n".join([text] + extra)


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def _prefix_in(source, haystack, step=200):
    """Length of the longest prefix of source found in haystack (to `step` characters)."""
    if not source:
        return 0
    if source in haystack:
        return len(source)
    lo, hi = 0, len(source)
    while hi - lo > step:
        mid = (lo + hi) // 2
        if source[:mid] in haystack:
            lo = mid
        else:
            hi = mid
    return lo


def clm_events(trial, uni):
    """Events of one CLM trial from its gateway log: each bash call's command, and the tool message that answered it
    (matched by tool_call_id in a later request)."""
    rows = T.rows(trial["calls_log"]) if trial.get("calls_log") else []
    tool_text = {}
    for r in rows:
        for m in (r.get("request") or {}).get("messages", []):
            if m.get("role") == "tool" and m.get("tool_call_id") and m["tool_call_id"] not in tool_text:
                c = m.get("content")
                tool_text[m["tool_call_id"]] = c if isinstance(c, str) else json.dumps(c)
    events = []
    for step, r in enumerate(rows, 1):
        msg = (((r.get("response") or {}).get("choices") or [{}])[0]).get("message") or {}
        for tc in msg.get("tool_calls") or []:
            cmd = clm_command((tc.get("function") or {}).get("arguments"))
            ev = {"step": step, "kind": "other", "docids": [], "returned": [], "admitted": [], "admitted_docs": []}
            shown = _decoded(tool_text.get(tc.get("id"), ""))
            sources = []
            if cmd and cmd[0] == "search" and cmd[1]:
                hits = uni.search(cmd[1])
                ev.update(kind="search", docids=[str(h["docid"]) for h in hits])
                sources = [(str(h["docid"]), "snippet", h.get("snippet") or "") for h in hits]
            elif cmd and cmd[0] == "document":
                ev.update(kind="document", docids=[cmd[1]])
                sources = [(cmd[1], "document", uni.document(cmd[1]))]
            for docid, kind, text in sources:
                ev["returned"] += uni.covered(docid, kind, [(0, len(text))])
                kept = uni.covered(docid, kind, [(0, _prefix_in(text, shown))])
                ev["admitted"] += kept
                if kept:
                    ev["admitted_docs"].append(docid)
            events.append(ev)
    return events


def repeats(events):
    """Within one trial: extra openings of the same document, and chunks returned / admitted more than once."""
    opened = collections.Counter(d for e in events if e["kind"] == "document" for d in e["docids"])
    returned = collections.Counter(h for e in events for h in e["returned"])
    admitted = collections.Counter(h for e in events for h in e["admitted"])
    return {"documents_opened": sum(opened.values()), "documents_distinct": len(opened),
            "document_reopens": sum(n - 1 for n in opened.values()),
            "chunks_returned": sum(returned.values()), "chunks_returned_distinct": len(returned),
            "chunks_returned_repeats": sum(n - 1 for n in returned.values()),
            "chunks_admitted": sum(admitted.values()), "chunks_admitted_distinct": len(admitted),
            "chunks_admitted_repeats": sum(n - 1 for n in admitted.values())}


def flop_rows(calls_log):
    """measure.flops rows for one log; for our arms, the server-tools copy (what the server templated) if present."""
    from measure.flops import measure_log
    from measure.models import MEASURED, load
    from measure.tokens import tokenizer_for_log
    log = Path(calls_log)
    copy = log.with_name("calls.server-tools.jsonl")
    log = copy if copy.exists() else log
    m = load(MEASURED)
    return measure_log(log, tokenizer_for_log(log, MEASURED), m["C_token"], m["C_attn"])["rows"]


def cut_replies(calls_log, rows_F):
    """Calls that hit the output cap with no valid tool call: count, their completion tokens, their share of FLOPs."""
    F = {r["call_no"]: r["F"] for r in rows_F}
    total = sum(F.values())
    cut = []
    for r in T.rows(calls_log):
        if not r.get("call_no") or r.get("status") != 200:
            continue
        choice = ((r.get("response") or {}).get("choices") or [{}])[0]
        if choice.get("finish_reason") != "length":
            continue
        valid = False
        for tc in (choice.get("message") or {}).get("tool_calls") or []:
            try:
                json.loads((tc.get("function") or {}).get("arguments") or "")
                valid = True
            except (json.JSONDecodeError, TypeError):
                pass
        if not valid:
            cut.append({"completion_tokens": (r.get("usage") or {}).get("completion_tokens", 0),
                        "F": F.get(r["call_no"], 0.0)})
    return {"cut_replies": len(cut), "cut_completion_tokens": sum(c["completion_tokens"] for c in cut),
            "cut_F": sum(c["F"] for c in cut), "total_F": total}


def recognition(events, record):
    """Did search return a gold document, was a chunk of it on the page the agent read (page 1 for paged search;
    the whole result otherwise, as far as it entered the prompt), and was it opened?"""
    gold = set(record["gold_docs"])
    searches = [e for e in events if e["kind"] == "search"]
    return {"gold_returned": any(set(e["docids"]) & gold for e in searches),
            "gold_on_page1": any(set(e["admitted_docs"]) & gold for e in searches),
            "gold_opened": any(set(e["docids"]) & gold for e in events if e["kind"] == "document")}
