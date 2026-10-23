"""Freeze private corpus text, source-preserving chunks, topics, and grader provenance."""

import ast
import json
import re
from pathlib import Path

from .common import (content, digest, load_experiment, private_path, read, settings,
                     write, write_rows)

TOPIC_PROMPT = (
    "Identify one to {limit} complementary information needs required to answer the question. "
    "Return only JSON: {{\"topics\": [{{\"id\": \"t1\", \"name\": \"date\", "
    "\"description\": \"when the event happened\"}}]}}. "
    "Use short, specific descriptions grounded only in the question. Do not guess the answer. "
    "Do not invent topics just to fill the limit."
)


def load_record(path, key, value):
    with Path(path).expanduser().open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                row = json.loads(line)
                if str(row.get(key)) == str(value):
                    return row
    raise ValueError(f"record not found in {Path(path).name}")


def chunk_document(text, docid, count, target=400):
    if not text or target < 1:
        raise ValueError("a non-empty document and positive chunk target are required")
    chunks, start = [], 0
    while start < len(text):
        lo, hi = start + 1, min(len(text), start + target * 8)
        while hi < len(text) and count(text[start:hi]) <= target:
            hi = min(len(text), start + 2 * (hi - start))
        end = start
        while lo <= hi:
            middle = (lo + hi) // 2
            if count(text[start:middle]) <= target:
                end, lo = middle, middle + 1
            else:
                hi = middle - 1
        if end == start:
            raise ValueError("chunk budget cannot fit one source character")
        if end < len(text):
            boundaries = [start + m.end() for m in re.finditer(r"\n\n|(?<=[.!?])\s+", text[start:end])]
            preferred = [b for b in boundaries if b - start >= (end - start) * 0.6
                         and count(text[start:b]) <= target]
            end = preferred[-1] if preferred else end
        fragment = text[start:end]
        ordinal = len(chunks)
        chunks.append({"chunk_id": f"c{ordinal:05d}", "docid": str(docid), "ordinal": ordinal,
                       "start": start, "end": end, "tokens": count(fragment), "text": fragment})
        start = end
    assert "".join(c["text"] for c in chunks) == text
    return chunks


def grader_protocol(source=None, revision=None, legacy=False):
    if legacy:
        from benchmarks.bcp.prompts import GRADER_TEMPLATE
        return {"protocol": "repository-legacy", "template": GRADER_TEMPLATE,
                "prompt_hash": digest(GRADER_TEMPLATE)}
    if not source or not revision or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("provide an upstream evaluator source file and its full commit SHA")
    source = Path(source).expanduser()
    template = None
    # Read the upstream constant without executing the evaluator or importing vLLM.
    for node in ast.parse(source.read_text()).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "GRADER_TEMPLATE"
                                                for t in node.targets):
            value = node.value
            if isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute) and value.func.attr == "strip":
                value = value.func.value
            template = ast.literal_eval(value).strip()
    if not isinstance(template, str) or not all("{" + k + "}" in template for k in
                                             ("question", "response", "correct_answer")):
        raise ValueError("upstream source has no supported GRADER_TEMPLATE")
    return {"protocol": "browsecomp-plus-upstream", "revision": revision,
            "source_url": f"https://github.com/texttron/BrowseComp-Plus/blob/{revision}/scripts_evaluation/evaluate_run.py",
            "source_hash": digest(source.read_text()), "prompt_hash": digest(template), "template": template}


def prepare(folder, question, document, *, query_id, docid, dataset_revision,
            selection_rationale, evaluation, protocol, tokens, cfg=None):
    folder, cfg = private_path(folder), settings(cfg)
    if not question.strip() or not document.strip() or not dataset_revision or not selection_rationale:
        raise ValueError("question, document, dataset revision, and selection rationale are required")
    if not isinstance(evaluation.get("answer"), str) or not evaluation["answer"].strip():
        raise ValueError("evaluation needs a non-empty gold answer")
    if evaluation.get("single_document_sufficient") is not True:
        raise ValueError("review and record single-document sufficiency before preparing")
    for span in evaluation.get("evidence_spans", []):
        if not (type(span.get("start")) is int and type(span.get("end")) is int
                and 0 <= span["start"] < span["end"] <= len(document)):
            raise ValueError("evidence spans must be valid source character intervals")
    fixture = {"query_id": str(query_id), "docid": str(docid), "question": question, "document": document}
    chunks = chunk_document(document, docid, tokens.count, cfg["chunk_tokens"])
    manifest = {"version": 1, "fixture_hash": digest(fixture), "document_hash": digest(document),
                "chunks_hash": digest(chunks), "document_tokens": tokens.count(document),
                "dataset_revision": dataset_revision, "selection_rationale": selection_rationale,
                "config": cfg, "config_hash": digest(cfg), "tokenizer": tokens.provenance,
                "implementation_hash": digest({p.name: digest(p.read_text()) for p in Path(__file__).parent.glob("*.py")}),
                "evaluation_hash": digest(evaluation), "grader_hash": digest(protocol)}
    write(folder / "fixture.json", fixture)
    write_rows(folder / "chunks.jsonl", chunks)
    write(folder / "evaluation.json", evaluation)
    write(folder / "grader.json", protocol)
    write(folder / "manifest.json", manifest)
    return manifest


def validate_topics(topics, limit=3):
    if not isinstance(topics, list) or not 1 <= len(topics) <= limit:
        raise ValueError("expected one to three topics")
    seen = set()
    for topic in topics:
        if not isinstance(topic, dict) or set(topic) != {"id", "name", "description"}:
            raise ValueError("topics need exactly id, name, and description")
        if any(not isinstance(v, str) or not v.strip() for v in topic.values()):
            raise ValueError("topic fields must be non-empty text")
        if not re.fullmatch(r"t[1-3]", topic["id"]) or topic["id"] in seen:
            raise ValueError("topic IDs must be unique t1, t2, or t3")
        seen.add(topic["id"])
    return topics


def propose_topics(folder, gateway):
    folder, manifest, fixture = load_experiment(folder)
    cfg = manifest["config"]
    path = folder / "topics.proposed.json"
    if path.exists():
        return read(path)
    if (folder / "score_attempts.jsonl").exists():
        raise ValueError("topic generation must precede scoring")
    gateway.preflight(cfg)
    if (folder / "topics-generation" / "calls.jsonl").exists():
        raise ValueError("topic generation was interrupted or invalid; review its trace before starting a new experiment")
    prompt = TOPIC_PROMPT.format(limit=cfg["max_topics"])
    reply = gateway.call([{"role": "system", "content": prompt},
                          {"role": "user", "content": fixture["question"]}],
                         {"model": cfg["model"], **cfg["sampling"]}, kind="topics")
    topics = validate_topics(json.loads(content(reply))["topics"], cfg["max_topics"])
    proposal = {"topics": topics, "prompt_hash": digest(prompt), "model": cfg["model"],
                "fixture_hash": manifest["fixture_hash"], "usage": reply.get("usage")}
    write(path, proposal)
    return proposal


def freeze_topics(folder, source, rationale):
    folder, manifest, _ = load_experiment(folder)
    if (folder / "score_attempts.jsonl").exists():
        raise ValueError("topics cannot change after scoring starts; use a new experiment")
    value = read(source)
    if value.get("fixture_hash", manifest["fixture_hash"]) != manifest["fixture_hash"]:
        raise ValueError("topic proposal belongs to a different question/document fixture")
    topics = validate_topics(value["topics"], manifest["config"]["max_topics"])
    if not rationale.strip():
        raise ValueError("record the topic review/edit rationale")
    frozen = {"topics": topics, "fixture_hash": manifest["fixture_hash"],
              "source_hash": digest(value), "review_rationale": rationale}
    write(folder / "topics.json", frozen)
    return frozen
