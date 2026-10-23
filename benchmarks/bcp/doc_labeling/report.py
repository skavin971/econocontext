"""Pinned BCP grading, evidence diagnostics, and honest token comparisons."""

import csv
import io
import json

from benchmarks.bcp.judge import exact_answer, normalize, parse

from .common import Gateway, content, digest, read, rows, usage_totals, write
from .rank import inputs


def subtract(left, right):
    return left - right if left is not None and right is not None else None


def grade(folder, result, manifest, fixture, evaluation, protocol, gateway_url):
    dest = folder / "sessions" / f"{result['condition']}.r{result['repeat']}"
    signature = digest({"response": result["response_text"], "protocol": protocol,
                        "gold": evaluation["answer"], "judge": manifest["config"]["judge"]})
    path = dest / "verdict.json"
    if path.exists():
        verdict = read(path)
        if verdict["signature"] != signature:
            raise ValueError("grading inputs changed")
        return verdict
    if result["status"] == "insufficient_evidence":
        verdict = {"correct": False, "parsed": True, "reason": "abstained"}
    elif result["status"] != "answered":
        return {"correct": None, "parsed": False, "reason": result["status"]}
    elif not gateway_url:
        return {"correct": None, "parsed": False, "reason": "not_judged"}
    else:
        gateway = Gateway(gateway_url, result["run_id"] + ".judge", dest / "judge")
        gateway.preflight(manifest["config"])
        response = gateway.call([{"role": "user", "content": protocol["template"].format(
            question=fixture["question"], response=result["response_text"], correct_answer=evaluation["answer"])}],
            manifest["config"]["judge"], kind="judge")
        text = content(response)
        correct = parse(text)
        verdict = {"correct": correct, "parsed": correct is not None, "text": text}
    verdict["signature"] = signature
    write(path, verdict)
    return verdict


def evidence_diagnostics(chunks, evaluation, rankings, page_sets):
    evidence = evaluation.get("evidence_spans", [])
    ids = {c["chunk_id"] for c in chunks for span in evidence
           if c["start"] < span["end"] and span["start"] < c["end"]}
    return {"annotated_chunk_ids": sorted(ids), "by_topic": [
        {"topic_id": r["topic"]["id"], "evidence_ranks": [
            {"chunk_id": c["chunk_id"], "rank": c["rank"], "score": c["score"]}
            for c in r["chunks"] if c["chunk_id"] in ids]} for r in rankings["rankings"]],
        "by_condition": {kind: [{"page": p["page"], "evidence_chunk_ids": sorted(ids.intersection(p["chunk_ids"]))}
                                for p in pages if ids.intersection(p["chunk_ids"])]
                         for kind, pages in page_sets.items()}}


def build_report(folder, gateway_url=None):
    folder, manifest, fixture, chunks, _ = inputs(folder)
    evaluation, protocol = read(folder / "evaluation.json"), read(folder / "grader.json")
    if digest(evaluation) != manifest["evaluation_hash"] or digest(protocol) != manifest["grader_hash"]:
        raise ValueError("evaluation or grader changed after preparation")
    schedule = read(folder / "schedule.json")
    summaries = []
    for item in schedule["sessions"]:
        path = folder / "sessions" / f"{item['condition']}.r{item['repeat']}" / "result.json"
        if not path.exists():
            continue
        result = read(path)
        if result["signature"] != schedule["signature"]:
            raise ValueError("session does not match frozen schedule")
        verdict = grade(folder, result, manifest, fixture, evaluation, protocol, gateway_url)
        exact = exact_answer(result["response_text"])
        summary = {k: result[k] for k in (
            "condition", "repeat", "run_id", "status", "pages_used", "model_calls", "invalid_actions",
            "unique_document_tokens_seen", "tool_output_tokens_delivered", "elapsed_ms",
            "total_model_input_tokens", "known_total_model_input_tokens", "total_model_output_tokens", "reasoning_tokens", "usage_complete")}
        summary.update(query_id=fixture["query_id"], docid=fixture["docid"],
                       document_tokens=manifest["document_tokens"], judged_correct=verdict.get("correct"),
                       judge_parsed=verdict.get("parsed", False),
                       exact_match=bool(exact) and normalize(exact) == normalize(evaluation["answer"]),
                       document_exposure_saved=manifest["document_tokens"] - result["unique_document_tokens_seen"],
                       document_exposure_reduction=1 - result["unique_document_tokens_seen"] / manifest["document_tokens"])
        evidence_ids = {c["chunk_id"] for c in chunks for span in evaluation.get("evidence_spans", [])
                        if c["start"] < span["end"] and span["start"] < c["end"]}
        summary["all_annotated_evidence_seen"] = evidence_ids.issubset(result["chunk_ids_seen"]) if evidence_ids else None
        # Evidence visibility/citation existence is diagnostic, not a claim of entailment.
        summary["citation_support_review"] = "pending_human_review"
        summaries.append(summary)
    for summary in summaries:
        def control(condition):
            return next((r for r in summaries if r["repeat"] == summary["repeat"] and r["condition"] == condition
                         and r["status"] == "answered" and r["model_calls"] > 0), {})
        full, original = control("full"), control("original")
        actual = summary["total_model_input_tokens"] if summary["model_calls"] else None
        summary["answerer_input_saved_vs_full"] = subtract(full.get("total_model_input_tokens"), actual)
        summary["answerer_input_saved_vs_original"] = subtract(original.get("total_model_input_tokens"), actual)
        summary["tool_output_saved_vs_full"] = subtract(full.get("tool_output_tokens_delivered"),
                                                       summary["tool_output_tokens_delivered"]) if summary["model_calls"] else None
    attempts = rows(folder / "score_attempts.jsonl")
    topic_calls = rows(folder / "topics-generation" / "calls.jsonl")
    report = {"version": 1, "config_hash": manifest["config_hash"], "fixture_hash": manifest["fixture_hash"],
              "preparation_implementation_hash": manifest["implementation_hash"],
              "execution_signature": schedule["signature"],
              "dataset_revision": manifest["dataset_revision"], "tokenizer": manifest["tokenizer"],
              "config": manifest["config"], "grader": {k: v for k, v in protocol.items() if k != "template"},
              "runs": summaries, "expected_sessions": len(schedule["sessions"]),
              "overhead": {"topics": {"calls": len(topic_calls), **usage_totals(topic_calls)},
                           "jev": {"attempts": len(attempts), "failed_attempts": sum(r["status"] == "failed" for r in attempts),
                                   "latency_ms": sum(r["latency_ms"] for r in attempts),
                                   "usage_by_request": [r.get("usage") for r in attempts]}},
              "evidence": evidence_diagnostics(chunks, evaluation, read(folder / "rankings.json"), {
                  kind: rows(folder / f"pages.{kind}.jsonl") for kind in ("ranked", "original")}),
              "interpretation": "Single-question case study; compare correctness and evidence with savings. "
                                "Jev and answering-model tokens use different units; no dollar savings inferred."}
    # Reports may be regenerated as sessions finish or grading becomes available.
    for name, text in (("summary.json", json.dumps(report, indent=2) + "\n"),
                       ("summary.csv", csv_report(summaries))):
        (folder / name).write_text(text, encoding="utf-8")
    return report


def csv_report(summaries):
    stream = io.StringIO()
    if summaries:
        writer = csv.DictWriter(stream, fieldnames=list(summaries[0]))
        writer.writeheader()
        writer.writerows(summaries)
    return stream.getvalue()
