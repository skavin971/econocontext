"""Pure round-robin ordering and token-bounded page construction."""

from .common import digest, read, write_rows
from .rank import inputs


def round_robin(rankings):
    seen, ordered = set(), []
    queues = [iter(r["chunks"]) for r in rankings]
    while queues:
        remaining = []
        for queue in queues:
            for chunk in queue:
                if chunk["chunk_id"] not in seen:
                    seen.add(chunk["chunk_id"])
                    ordered.append(chunk)
                    remaining.append(queue)
                    break
        queues = remaining
    return ordered


def render(chunks):
    return "\n\n".join(f"[document {c['docid']} chunk {c['chunk_id']} chars {c['start']}:{c['end']}]\n{c['text']}"
                       for c in chunks)


def pack(chunks, count, cap=2000):
    pages, current = [], []
    if len({c["chunk_id"] for c in chunks}) != len(chunks):
        raise ValueError("page sequence contains duplicate chunk IDs")
    for chunk in chunks:
        if count(render([chunk])) > cap:
            raise ValueError("a chunk including its header exceeds the page cap")
        if current and count(render(current + [chunk])) > cap:
            pages.append(current)
            current = []
        current.append(chunk)
    if current:
        pages.append(current)
    return [{"page": i + 1, "chunk_ids": [c["chunk_id"] for c in page],
             "text": render(page), "tokens": count(render(page))} for i, page in enumerate(pages)]


def build_pages(folder, tokens):
    folder, manifest, _, chunks, frozen = inputs(folder)
    ranked = read(folder / "rankings.json")
    if ranked["chunks_hash"] != manifest["chunks_hash"] or ranked["topics_hash"] != digest(frozen):
        raise ValueError("rankings are stale")
    lookup = {c["chunk_id"]: c for c in chunks}
    for ranking in ranked["rankings"]:
        ids = [c["chunk_id"] for c in ranking["chunks"]]
        if len(ids) != len(chunks) or set(ids) != set(lookup):
            raise ValueError("each topic must rank every chunk exactly once")
        for c in ranking["chunks"]:
            if any(c[k] != v for k, v in lookup[c["chunk_id"]].items()):
                raise ValueError("ranked chunk text or provenance changed")
    result = {}
    for condition, ordered in (("ranked", round_robin(ranked["rankings"])), ("original", chunks)):
        pages = pack(ordered, tokens.count, manifest["config"]["page_tokens"])
        write_rows(folder / f"pages.{condition}.jsonl", pages)
        result[condition] = pages
    write_rows(folder / "pages.jsonl", [{"condition": kind, **p} for kind, pages in result.items() for p in pages])
    return result
