"""Check a new environment against the committed parity fixtures (no BCP text; docids and hashes only).

What it checks, against tests/fixtures/parity.json:
1. Search: about 20 neutral queries (written for this check, not derived from any BrowseComp-Plus question) return
   the same top-10 docids, in order, and the same snippet sha256s from the Lucene search service.
2. Chunks: 5 documents' 8,192-token views cut into the arms' 400-token document chunks (prepare.chunk_document with
   the main model's tokenizer, as Study 2 cut them) and into 102-token snippet chunks: same chunk ids, char spans and
   text sha256s.
3. Tokens: fixed strings counted by the Qwen3-0.6B cutting tokenizer and by the main model's tokenizer.
4. FLOPs: CLM Eq. 9 prefix-reuse FLOPs (measure/flops.py) of a synthetic call log, to the FLOP.

Use:
  .venv/bin/python scripts/check_parity.py --search-url http://127.0.0.1:8790          # check
  .venv/bin/python scripts/check_parity.py --search-url http://127.0.0.1:8790 --write  # (re)record the fixtures
Exit status 0 when everything matches.
"""

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FIXTURE = ROOT / "tests" / "fixtures" / "parity.json"

# Neutral queries, written by hand for this check; none is derived from a BrowseComp-Plus question.
QUERIES = [
    "history of the printing press", "photosynthesis in desert plants", "how glaciers shape valleys",
    "jazz musicians of the 1950s", "volcanic eruption in Iceland", "rules of cricket", "origin of the violin",
    "migration of monarch butterflies", "construction of suspension bridges", "medieval castle architecture",
    "Olympic marathon record", "coral reef bleaching", "invention of the telephone", "Antarctic research station",
    "football club founded in 1899", "symphony orchestra conductor", "silk road trade routes",
    "lighthouse keeper history", "chess world championship match", "railway tunnel through the Alps",
]
STRINGS = ["Hello, world!", "The quick brown fox jumps over the lazy dog.", "1,234.56 kg of 2026-10-26 data",
           "naïve café — émigré résumé", "def f(x):\n    return x * 2\n", "東京タワー と 富士山"]


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def synthetic_log(path):
    """Three calls of one growing conversation, no BCP text; usage has no prompt_token_ids (local rebuild only)."""
    system = {"role": "system", "content": "You are a careful assistant. Answer briefly."}
    turns = [{"role": "user", "content": "List three primary colors."}]
    replies = ["Red, yellow and blue.", "Mixing red and blue gives purple.", "Done."]
    rows = []
    for i, reply in enumerate(replies, 1):
        request = {"model": "qwen3.8:27b", "messages": [system] + turns}
        rows.append({"call_no": i, "kind": "agent", "status": 200, "request": request,
                     "response": {"choices": [{"message": {"role": "assistant", "content": reply}}]},
                     "usage": {"prompt_tokens": 0, "completion_tokens": 12 + i}})
        turns = turns + [{"role": "assistant", "content": reply}, {"role": "user", "content": f"Next step {i}."}]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def compute(search_url):
    from transformers import AutoTokenizer

    from benchmarks.bcp.doc_labeling.common import Tokens, settings
    from benchmarks.bcp.doc_labeling.prepare import chunk_document
    from benchmarks.bcp.doc_labeling.search_pilot import LuceneCorpus
    from measure.flops import measure_log
    from measure.models import MEASURED, load

    corpus = LuceneCorpus(search_url)
    main = Tokens(settings({"repeats": 1}))
    small = AutoTokenizer.from_pretrained("Qwen/Qwen3-0.6B")
    count_small = lambda t: len(small.encode(t, add_special_tokens=False))
    out = {"search": [], "chunks": [], "tokens": [], "flops": None,
           "provenance": {"main_tokenizer": main.provenance, "cutting_tokenizer": "Qwen/Qwen3-0.6B"}}
    docids = []
    for q in QUERIES:
        hits = corpus.search(q)
        out["search"].append({"query": q, "docids": [str(h["docid"]) for h in hits],
                              "snippet_sha256": [sha(h.get("snippet") or "") for h in hits]})
        docids += [str(h["docid"]) for h in hits[:1]]
    for docid in docids[:5]:
        view = corpus.document(docid)
        row = {"docid": docid, "view_sha256": sha(view), "view_tokens_small": count_small(view)}
        for name, count, size in (("document_400_main", main.count, 400), ("snippet_102_small", count_small, 102)):
            row[name] = [[c["chunk_id"], c["start"], c["end"], sha(c["text"])]
                         for c in chunk_document(view, docid, count, size)]
        out["chunks"].append(row)
    out["tokens"] = [{"text": s, "small": count_small(s), "main": main.count(s)} for s in STRINGS]
    with tempfile.TemporaryDirectory() as tmp:
        log = Path(tmp) / "calls.jsonl"
        synthetic_log(log)
        m = load(MEASURED)
        totals = measure_log(log, main.tok, m["C_token"], m["C_attn"])["totals"]
        out["flops"] = {k: totals[k] for k in ("calls", "P", "R", "U", "G", "F")}
    return out


def compare(want, got):
    problems = []
    for w, g in zip(want["search"], got["search"]):
        if w != g:
            problems.append(f"search differs for {w['query']!r}")
    for w, g in zip(want["chunks"], got["chunks"]):
        for k in w:
            if w[k] != g.get(k):
                problems.append(f"chunks differ for document {w['docid']}: {k}")
    for w, g in zip(want["tokens"], got["tokens"]):
        if w != g:
            problems.append(f"token counts differ for {w['text']!r}: {w} vs {g}")
    if want["flops"] != got["flops"]:
        problems.append(f"Eq. 9 FLOPs differ: {want['flops']} vs {got['flops']}")
    if len(want["search"]) != len(got["search"]) or len(want["chunks"]) != len(got["chunks"]):
        problems.append("fixture sizes differ")
    return problems


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--search-url", default="http://127.0.0.1:8790")
    ap.add_argument("--write", action="store_true", help="record the fixtures from this environment")
    a = ap.parse_args()
    got = compute(a.search_url)
    if a.write:
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(json.dumps(got, indent=1, ensure_ascii=False) + "\n")
        print(f"wrote {FIXTURE}")
        return 0
    problems = compare(json.loads(FIXTURE.read_text()), got)
    for p in problems:
        print("MISMATCH", p)
    print("parity OK" if not problems else f"{len(problems)} mismatches")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
