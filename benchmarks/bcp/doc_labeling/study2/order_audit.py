"""Offline order audit: what Jev's page 1 shows vs the page 1 a free order would have shown (read-only, no model calls).

For every paged unit an arm served (a search's snippet chunks, or an opened document's chunks), the saved
pages.json holds every chunk (nothing is dropped), each rendered as "[document D chunk C chars a:b]\\n<text>".
parse_pages() rebuilds the chunks from that text, so Strict Rank units (whose rankings.json keeps no text) and
Score + Interleave units are audited the same way. Page 1 under a free order is rebuilt with the arms' own
pages.pack() at the same 2,000-token cap and tokenizer:
- hit order: s000, s001, ... (search: the ten hits in rank order, each hit's snippet chunks in order);
- lead-first: every hit's first chunk, then every hit's second chunk, ...;
- original order: a document's chunks in document order (what the `original` arm served).
Gold and evidence docids and the answer string are evaluation-only and are read only after trials end.
"""

import collections
import json
import re
import statistics
from pathlib import Path

from benchmarks.bcp.doc_labeling.pages import pack
from benchmarks.bcp.judge import normalize

HEADER = re.compile(r"\[document (\S+) chunk (\S+) chars (\d+):(\d+)\]\n")
SEARCH_ARMS = ("ranked_doc_search", "ranked_doc_search_labels", "strict_rank_search_labels",
               "strict_rank_search_reasoning")
DOC_ARMS = ("ranked_doc",) + SEARCH_ARMS


def parse_pages(pages, check=True):
    """chunk_id -> {docid, chunk_id, start, end, text, ordinal} from rendered pages (pages.render's format)."""
    out = {}
    for page in pages:
        text = page["text"]
        marks = list(HEADER.finditer(text))
        for i, m in enumerate(marks):
            end = marks[i + 1].start() - 2 if i + 1 < len(marks) else len(text)  # render joins chunks with "\n\n"
            cid = m.group(2)
            out[cid] = {"docid": m.group(1), "chunk_id": cid, "start": int(m.group(3)), "end": int(m.group(4)),
                        "text": text[m.end():end], "ordinal": int(re.sub(r"\D", "", cid))}
    if check and sum(len(p["chunk_ids"]) for p in pages) != len(out):
        raise ValueError("pages.json chunk count does not match its text")
    return out


def hit_order(chunks):
    return sorted(chunks, key=lambda c: c["ordinal"])


def lead_first(chunks):
    """Every hit's 1st chunk (in hit order), then every hit's 2nd chunk, ..."""
    by_doc = collections.OrderedDict()
    for c in hit_order(chunks):
        by_doc.setdefault(c["docid"], []).append(c)
    out, k = [], 0
    while any(len(v) > k for v in by_doc.values()):
        out += [v[k] for v in by_doc.values() if len(v) > k]
        k += 1
    return out


def first_page(chunks, count, cap=2000):
    return set(pack(list(chunks), count, cap)[0]["chunk_ids"]) if chunks else set()


def units(trial_dir, kind):
    """(unit key, pages) for each paged search ('search') or opened document view ('documents') of one trial."""
    pattern = "search/*/pages.json" if kind == "search" else "documents/*/*/pages.json"
    for f in sorted(Path(trial_dir).glob(pattern)):
        yield f.parent.relative_to(trial_dir).as_posix(), json.loads(f.read_text())


def audit_trial(trial, record, count, cap=2000):
    """Per-unit page-1 comparisons for one ranked trial. Returns {'search': [...], 'documents': [...]}."""
    relevant = set(record["gold_docs"]) | set(record["evidence_docs"])
    answer = normalize(record["answer"])
    out = {"search": [], "documents": []}
    if trial["arm"] in SEARCH_ARMS:
        for key, pages in units(trial["trial_dir"], "search"):
            ch = parse_pages(pages)
            jev = set(pages[0]["chunk_ids"])
            hit = first_page(hit_order(ch.values()), count, cap)
            lead = first_page(lead_first(ch.values()), count, cap)
            row = {"key": key, "chunks": len(ch), "jev_p1": jev, "hit_p1": hit, "lead_p1": lead,
                   "overlap_hit": len(jev & hit) / len(jev), "overlap_lead": len(jev & lead) / len(jev),
                   "relevant": [], "answer_chunks": {c["chunk_id"] for c in ch.values()
                                                     if answer and answer in normalize(c["text"])},
                   "docid_of": {c["chunk_id"]: c["docid"] for c in ch.values()}}
            for d in sorted({c["docid"] for c in ch.values()} & relevant):
                ids = sorted((c for c in ch.values() if c["docid"] == d), key=lambda c: c["ordinal"])
                first, every = ids[0]["chunk_id"], {c["chunk_id"] for c in ids}
                row["relevant"].append({"docid": d, "gold": d in record["gold_docs"],
                                        "first": {o: first in s for o, s in (("jev", jev), ("hit", hit), ("lead", lead))},
                                        "any": {o: bool(every & s) for o, s in (("jev", jev), ("hit", hit), ("lead", lead))}})
            out["search"].append(row)
    if trial["arm"] in DOC_ARMS:
        for key, pages in units(trial["trial_dir"], "documents"):
            ch = parse_pages(pages)
            docid = key.split("/")[1]
            jev = set(pages[0]["chunk_ids"])
            orig = first_page(hit_order(ch.values()), count, cap)
            ans = {c["chunk_id"] for c in ch.values() if answer and answer in normalize(c["text"])}
            out["documents"].append({
                "key": key, "docid": docid, "all": set(ch), "kind": "gold" if docid in record["gold_docs"] else
                ("evidence" if docid in relevant else "other"), "chunks": len(ch),
                "jev_positions": sorted(ch[c]["ordinal"] + 1 for c in jev), "jev_p1": jev, "orig_p1": orig,
                "overlap_orig": len(jev & orig) / len(jev),
                "answer_positions": sorted(ch[c]["ordinal"] + 1 for c in ans),
                "answer_on_p1": {"jev": bool(ans & jev), "orig": bool(ans & orig)} if ans else None})
    return out


def citations(trial, audit):
    """Map each citation of an answered trial to the unit chunk it names. A citation is 'docid:chunk_id' or a bare
    docid (a whole snippet or document; not mappable to a chunk). Returns per-citation rows."""
    rows = []
    for c in (trial["summary"].get("answer") or {}).get("citations", []):
        if ":" not in c:
            rows.append({"citation": "bare-docid", "mapped": False})
            continue
        docid, cid = c.split(":", 1)
        if cid.startswith("s"):
            cands = [(u["jev_p1"], u["hit_p1"], u["lead_p1"]) for u in audit["search"] if u["docid_of"].get(cid) == docid]
        else:
            cands = [(u["jev_p1"], u["orig_p1"], u["orig_p1"]) for u in audit["documents"]
                     if u["docid"] == docid and cid in u["all"]]
        if not cands:
            rows.append({"citation": "unmapped", "mapped": False})
            continue
        jev = any(cid in j for j, _, _ in cands)
        free = any(cid in h for _, h, _ in cands)
        lead = any(cid in l for _, _, l in cands)
        rows.append({"citation": "search" if cid.startswith("s") else "document", "mapped": True,
                     "on_jev_p1": jev, "on_free_p1": free, "on_lead_p1": lead, "only_jev": jev and not free})
    return rows


def summarize(per_trial):
    """Pooled counts over many audit_trial() results (aggregates only)."""
    s = {"searches": 0, "overlap_hit": [], "overlap_lead": [], "relevant": 0,
         "first": collections.Counter(), "any": collections.Counter(), "answer_searches": 0,
         "answer": collections.Counter(), "documents": 0, "overlap_orig": [], "doc_answer": collections.Counter(),
         "doc_answer_n": 0}
    for audit in per_trial:
        for u in audit["search"]:
            s["searches"] += 1
            s["overlap_hit"].append(u["overlap_hit"])
            s["overlap_lead"].append(u["overlap_lead"])
            for r in u["relevant"]:
                s["relevant"] += 1
                s["first"].update(k for k, v in r["first"].items() if v)
                s["any"].update(k for k, v in r["any"].items() if v)
            if u["answer_chunks"]:
                s["answer_searches"] += 1
                s["answer"].update(k for k, p in (("jev", u["jev_p1"]), ("hit", u["hit_p1"]), ("lead", u["lead_p1"]))
                                   if u["answer_chunks"] & p)
        for d in audit["documents"]:
            s["documents"] += 1
            s["overlap_orig"].append(d["overlap_orig"])
            if d["answer_on_p1"]:
                s["doc_answer_n"] += 1
                s["doc_answer"].update(k for k, v in d["answer_on_p1"].items() if v)
    for k in ("overlap_hit", "overlap_lead", "overlap_orig"):
        v = s[k]
        s[k] = {"median": statistics.median(v), "mean": statistics.mean(v), "min": min(v), "max": max(v)} if v else None
    return s
