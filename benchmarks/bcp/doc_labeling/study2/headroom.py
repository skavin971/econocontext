"""Phase 0, T5: where the answer sits in the gold documents (offline; dev questions; no model calls).

For each of the 50 dev questions (benchmarks/bcp/split.json) and each of its gold documents, take the document's
8,192-token view from the search service (the view every arm reads, cut by Qwen3-0.6B) and find the first
occurrence of the normalized gold answer, matched on whole words (judge.normalize: lower case, punctuation and
articles removed, spaces collapsed). The offset is the 1-based Qwen3-0.6B token position where the match starts.
Buckets:
  snippet   offset <= 512            (inside the search snippet: the first 512 tokens)
  page1     513-2,000                (page 1 in original order)
  deep      2,001-8,192              (needs paging or ranking)
  absent    not in the view
Per question, the earliest bucket over its gold documents. A proxy only: the answer string ignores the supporting
evidence the question's other clues need, and a string can occur by coincidence.
"""

import bisect
import json
import re

from benchmarks.bcp.judge import normalize

from . import trials as T

BUCKETS = (("snippet", 512), ("page1", 2000), ("deep", 8192))


def bucket(offset):
    if offset is None:
        return "absent"
    for name, limit in BUCKETS:
        if offset <= limit:
            return name
    return "absent"


def first_offset(view, answer, tok):
    """1-based token position of the first whole-word match of the normalized answer in the view, or None."""
    a = normalize(answer)
    if not a or not view:
        return None
    norm = normalize(view)
    m = re.search(r"(?:^| )" + re.escape(a) + r"(?: |$)", norm)
    if not m:
        return None
    target = m.start() + (1 if norm[m.start()] == " " else 0)
    ends = [e for _, e in tok(view, add_special_tokens=False, return_offsets_mapping=True)["offset_mapping"]]
    # the first token whose normalized prefix reaches past the match start
    lengths = _Lengths(view, ends)
    return bisect.bisect_right(lengths, target) + 1


class _Lengths:
    """Lazy sequence: len(normalize(view[:end_i])) for token end offsets (monotone up to normalization edges)."""

    def __init__(self, view, ends):
        self.view, self.ends = view, ends

    def __len__(self):
        return len(self.ends)

    def __getitem__(self, i):
        return len(normalize(self.view[:self.ends[i]]))


def dev_ids(split_path=None):
    from pathlib import Path
    path = Path(split_path) if split_path else Path(__file__).resolve().parents[2] / "split.json"
    split = json.loads(path.read_text())
    return [str(q) for q in split["dev"]], [str(q) for q in split["pilot"]]


def headroom(corpus, tok, bcp=T.BCP):
    recs = T.records(bcp)
    dev, pilot = dev_ids()
    out = []
    for qid in dev:
        rec = recs[qid]
        docs = []
        for docid in rec["gold_docs"]:
            view = corpus.document(docid) or ""
            off = first_offset(view, rec["answer"], tok)
            docs.append({"docid": docid, "view_tokens": len(tok.encode(view, add_special_tokens=False)),
                         "offset": off, "bucket": bucket(off)})
        order = ["snippet", "page1", "deep", "absent"]
        earliest = min((d["bucket"] for d in docs), key=order.index) if docs else "absent"
        out.append({"qid": qid, "pilot": qid in pilot, "gold_docs": len(docs), "earliest": earliest, "docs": docs})
    return out
