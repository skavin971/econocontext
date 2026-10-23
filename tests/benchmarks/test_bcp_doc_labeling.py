"""Synthetic pilot experiments; no tokenizer downloads, credentials, or live APIs."""

import copy
import io
import json
from unittest.mock import patch

import pytest

from benchmarks.bcp.doc_labeling.common import (Gateway, GatewayError, Tokens, digest, private_path,
                                               read, rows, settings, usage_totals, write)
from benchmarks.bcp.doc_labeling.pages import build_pages, pack, round_robin
from benchmarks.bcp.doc_labeling.prepare import (chunk_document, freeze_topics,
                                                grader_protocol, prepare, propose_topics, validate_topics)
from benchmarks.bcp.doc_labeling.rank import score
from benchmarks.bcp.doc_labeling.report import build_report
from benchmarks.bcp.doc_labeling.run import action, run, run_session


class CharTokens:
    """Character counting makes source reconstruction/token caps transparent."""
    provenance = {"hf_id": "synthetic", "revision": "test", "provider": "purdue"}

    def count(self, text):
        return len(text)

    def coverage(self, text, spans):
        return sum(any(a <= i < b for a, b in spans) for i in range(len(text)))

    def prompt(self, messages, tools):
        return len(json.dumps(messages)) + len(json.dumps(tools))


class FakeJev:
    model, max_input_tokens = "fake-jev", 28000

    def __init__(self):
        self.seen = []

    def fit(self, state, questions):
        return state

    def ask(self, state, questions):
        self.seen.append(copy.deepcopy(state))
        return {key: {"noul": 0.9 if "evidence" in state["chunk"] else 0.1} for key in questions}, {"input_tokens": 10}


@pytest.fixture
def pilot(tmp_path):
    tokens = CharTokens()
    cfg = settings({"chunk_tokens": 40, "page_tokens": 180, "repeats": 1,
                    "context_tokens": 100000, "sampling": {"temperature": 0.7, "max_tokens": 32}})
    text = "Background information. " * 8 + "Important evidence happened in 1900. " + "More details. " * 6
    prepare(tmp_path, "When did the event happen?", text, query_id="7", docid="42",
            dataset_revision="synthetic-v1", selection_rationale="synthetic evidence document",
            evaluation={"answer": "1900", "single_document_sufficient": True,
                        "evidence_spans": [{"start": text.index("Important"), "end": text.index("1900") + 4}]},
            protocol=grader_protocol(legacy=True), tokens=tokens, cfg=cfg)
    topics = {"topics": [{"id": "t1", "name": "date", "description": "when the event happened"},
                         {"id": "t2", "name": "identity", "description": "identify the event"}]}
    write(tmp_path / "proposal.json", topics)
    freeze_topics(tmp_path, tmp_path / "proposal.json", "reviewed before scoring")
    return tmp_path, tokens, cfg


def tool_reply(name, args=None, usage=None):
    if args is None:
        args = {} if name == "next_page" else {
            "explanation": "The evidence states the date.", "exact_answer": "1900", "confidence": 80,
            "citations": [], "insufficient_evidence": False}
    return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": "call1", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}}],
        "usage": usage or {"prompt_tokens": 100, "completion_tokens": 10}}


def test_chunks_reconstruct_unicode_and_unbroken_text():
    text = "Heading\n\n" + "One sentence. " * 10 + "漢字🙂" * 80
    chunks = chunk_document(text, "doc", len, target=40)
    assert "".join(c["text"] for c in chunks) == text
    assert all(c["tokens"] <= 40 and text[c["start"]:c["end"]] == c["text"] for c in chunks)
    assert [c["ordinal"] for c in chunks] == list(range(len(chunks)))


def test_topics_validate_and_freeze_before_scoring(pilot):
    folder, _, _ = pilot
    for bad in ([], [{"id": "t1", "name": "x"}], [dict(id="t1", name="x", description="y")] * 2):
        with pytest.raises(ValueError):
            validate_topics(bad)
    score(folder, FakeJev())
    with pytest.raises(ValueError, match="after scoring"):
        freeze_topics(folder, folder / "proposal.json", "later edit")


def test_rank_is_independent_complete_and_resumable(pilot):
    folder, _, _ = pilot
    jev = FakeJev()
    ranked = score(folder, jev)
    chunks = rows(folder / "chunks.jsonl")
    assert len(jev.seen) == len(chunks)
    assert len(rows(folder / "scores.jsonl")) == len(chunks) * 2
    assert ranked["rankings"][0]["chunks"][0]["score"] == 0.9
    assert sum(c["score"] for c in ranked["rankings"][0]["chunks"]) > 1
    assert all("answer" not in state and "evaluation" not in state for state in jev.seen)
    assert score(folder, jev) == ranked
    assert len(jev.seen) == len(chunks)


@pytest.mark.parametrize("bad", [None, -0.1, 1.1, float("nan"), True])
def test_invalid_scores_fail_instead_of_becoming_zero(pilot, bad):
    folder, _, _ = pilot
    jev = FakeJev()
    jev.ask = lambda state, questions: ({k: {"noul": bad} for k in questions}, {})
    with pytest.raises(RuntimeError, match="incomplete"):
        score(folder, jev)
    assert not (folder / "scores.jsonl").exists()
    assert rows(folder / "score_attempts.jsonl")[0]["status"] == "failed"


def test_transient_scoring_failure_can_retry(pilot):
    folder, _, _ = pilot
    jev, attempts = FakeJev(), []
    ask = jev.ask

    def flaky(state, questions):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("Jev HTTP 503")
        return ask(state, questions)
    jev.ask = flaky
    delays = []
    score(folder, jev, sleep=delays.append)
    assert delays == [1]
    assert rows(folder / "score_attempts.jsonl")[0]["status"] == "failed"


def test_scoring_refuses_truncation(pilot):
    folder, _, _ = pilot
    jev = FakeJev()
    jev.fit = lambda state, questions: {"question": "cut"}
    with pytest.raises(ValueError, match="truncate"):
        score(folder, jev)


def test_round_robin_global_dedup_and_full_coverage(pilot):
    folder, tokens, cfg = pilot
    ranked = score(folder, FakeJev())
    ordered = round_robin(ranked["rankings"])
    ids = [c["chunk_id"] for c in ordered]
    assert len(ids) == len(set(ids)) == len(rows(folder / "chunks.jsonl"))
    pages = build_pages(folder, tokens)
    assert all(p["tokens"] <= cfg["page_tokens"] for ps in pages.values() for p in ps)
    assert {c for p in pages["ranked"] for c in p["chunk_ids"]} == set(ids)
    with pytest.raises(ValueError, match="header"):
        pack(ordered, len, cap=10)
    with pytest.raises(ValueError, match="duplicate"):
        pack(ordered + ordered[:1], len)


def test_ranking_ties_are_deterministic_and_sources_are_checked(pilot):
    folder, tokens, _ = pilot
    score(folder, FakeJev())
    ranked = read(folder / "rankings.json")
    assert all([c["ordinal"] for c in r["chunks"] if c["score"] == 0.1] ==
               sorted(c["ordinal"] for c in r["chunks"] if c["score"] == 0.1) for r in ranked["rankings"])
    ranked["rankings"][0]["chunks"][0]["text"] = "tampered"
    (folder / "rankings.json").write_text(json.dumps(ranked))
    with pytest.raises(ValueError, match="changed"):
        build_pages(folder, tokens)


def test_answer_loop_stops_and_counts_repeated_input(pilot):
    folder, tokens, cfg = pilot
    score(folder, FakeJev())
    pages = build_pages(folder, tokens)["ranked"]
    seen = []

    def answerer(messages, sampling, **kwargs):
        seen.append(copy.deepcopy(messages))
        return tool_reply("next_page" if len(seen) == 1 else "answer",
                          usage={"prompt_tokens": 100 if len(seen) == 1 else 300, "completion_tokens": 10})
    result = run_session("question", pages, rows(folder / "chunks.jsonl"), cfg, tokens, answerer, condition="ranked")
    assert result["status"] == "answered" and result["pages_used"] == 2
    assert result["total_model_input_tokens"] == 400
    assert pages[0]["text"] in json.dumps(seen[1]) or seen[1][2]["content"] == pages[0]["text"]
    assert result["tool_output_tokens_delivered"] == sum(p["tokens"] for p in pages[:2])


def test_unshown_page_at_call_limit_is_not_counted(pilot):
    folder, tokens, cfg = pilot
    score(folder, FakeJev())
    pages = build_pages(folder, tokens)["original"]
    cfg = {**cfg, "max_calls": 1}
    result = run_session("q", pages, rows(folder / "chunks.jsonl"), cfg, tokens,
                         lambda *a, **k: tool_reply("next_page"), condition="original")
    assert result["status"] == "call_limit" and result["pages_used"] == 1


def test_exhaustion_offers_final_abstention(pilot):
    folder, tokens, cfg = pilot
    chunks = rows(folder / "chunks.jsonl")
    pages = pack(chunks[:1], tokens.count, 180)
    observed = []

    def answerer(messages, sampling, tools):
        observed.append(copy.deepcopy(tools))
        return tool_reply("next_page") if len(observed) == 1 else tool_reply("answer", {
            "explanation": "Need another source", "exact_answer": "", "confidence": 0,
            "citations": [], "insufficient_evidence": True})
    result = run_session("q", pages, chunks, cfg, tokens, answerer, condition="ranked")
    assert result["status"] == "insufficient_evidence"
    assert len(observed[1]) == 1 and observed[1][0]["function"]["name"] == "answer"


def test_context_refusal_and_transport_failure_are_distinct(pilot):
    folder, tokens, cfg = pilot
    chunks = rows(folder / "chunks.jsonl")
    pages = pack(chunks, tokens.count, 180)
    result = run_session("q", pages, chunks, {**cfg, "context_tokens": 1}, tokens,
                         lambda *a, **k: pytest.fail("must not call"), condition="full")
    assert result["status"] == "context_limit" and result["pages_used"] == 0
    def fail(*args, **kwargs):
        raise GatewayError("HTTP 500")
    failed = run_session("q", pages, chunks, cfg, tokens, fail, condition="ranked")
    assert failed["status"] == "error" and failed["total_model_input_tokens"] is None


def test_incomplete_usage_and_malformed_action_are_visible(pilot):
    folder, tokens, cfg = pilot
    assert usage_totals([{"response": {"usage": {"prompt_tokens": 5}}}])["total_model_output_tokens"] is None
    with pytest.raises(ValueError):
        action({"tool_calls": []}, {"answer"})
    pages = pack(rows(folder / "chunks.jsonl"), tokens.count, 180)
    bad = {"choices": [{"message": {"content": "just prose"}}]}
    result = run_session("q", pages, rows(folder / "chunks.jsonl"), cfg, tokens,
                         lambda *a, **k: bad, condition="original")
    assert result["invalid_actions"] == cfg["invalid_limit"] and result["status"] == "error"


def test_full_offline_pipeline_and_report_resume(pilot):
    folder, tokens, _ = pilot
    score(folder, FakeJev())

    class FakeGateway:
        def __init__(self, url, run_id, dest):
            self.run_id = run_id
        def preflight(self, cfg):
            pass
        def call(self, *args, **kwargs):
            return tool_reply("answer")
    run(folder, tokens, gateway_factory=FakeGateway)
    with patch.object(FakeGateway, "call", side_effect=AssertionError("must resume")):
        run(folder, tokens, gateway_factory=FakeGateway)
    report = build_report(folder)
    assert len(report["runs"]) == 3
    assert all(r["exact_match"] for r in report["runs"])
    assert all(r["judged_correct"] is None for r in report["runs"])
    assert "1900" not in json.dumps(report)
    assert (folder / "summary.csv").read_text().startswith("condition,repeat,")


def test_pinned_upstream_grader_is_read_without_execution(tmp_path):
    source = tmp_path / "evaluate.py"
    source.write_text('raise RuntimeError("do not execute")\nGRADER_TEMPLATE = "{question} {response} {correct_answer}".strip()\n')
    protocol = grader_protocol(source, "a" * 40)
    assert protocol["revision"] == "a" * 40 and protocol["prompt_hash"] == digest(protocol["template"])
    with pytest.raises(ValueError):
        grader_protocol(source, "main")


def test_benchmark_artifacts_cannot_be_saved_in_repo():
    with pytest.raises(ValueError, match="outside"):
        private_path("data/bcp-pilot")


def test_topic_generator_only_receives_the_question(pilot):
    folder, _, _ = pilot
    seen = []
    class TopicGateway:
        def preflight(self, cfg):
            pass
        def call(self, messages, sampling, **kwargs):
            seen.append(messages)
            return {"choices": [{"message": {"content": json.dumps({"topics": [
                {"id": "t1", "name": "date", "description": "when it happened"}]})}}]}
    proposed = propose_topics(folder, TopicGateway())
    assert proposed["topics"][0]["id"] == "t1"
    assert seen[0][1]["content"] == "When did the event happen?"
    assert "1900" not in json.dumps(seen)
    propose_topics(folder, TopicGateway())
    assert len(seen) == 1


def test_live_grading_is_separate_and_unparsed_verdicts_resume(pilot):
    folder, tokens, _ = pilot
    score(folder, FakeJev())
    class AnswerGateway:
        def __init__(self, url, run_id, dest):
            self.run_id = run_id
        def preflight(self, cfg):
            pass
        def call(self, *args, **kwargs):
            return tool_reply("answer")
    run(folder, tokens, gateway_factory=AnswerGateway)
    judged = []
    class JudgeGateway(AnswerGateway):
        def call(self, messages, sampling, **kwargs):
            judged.append((self.run_id, messages, sampling, kwargs))
            return {"choices": [{"message": {"content": "correct: uncertain"}}]}
    with patch("benchmarks.bcp.doc_labeling.report.Gateway", JudgeGateway):
        first = build_report(folder, gateway_url="fake")
        second = build_report(folder, gateway_url="fake")
    assert len(judged) == 3
    assert all(j[0].endswith(".judge") and j[3]["kind"] == "judge" for j in judged)
    assert all("1900" in j[1][0]["content"] for j in judged)
    assert all(r["judged_correct"] is None and not r["judge_parsed"] for r in first["runs"])
    assert first["runs"] == second["runs"]


def test_gateway_preflight_rejects_public_logs_and_mismatched_sampling(tmp_path):
    cfg = settings()
    health = {"provider": "purdue", "log_dir": str(tmp_path / "gateway"),
              "upstream": "fake", "max_tokens": 4096}
    gateway = Gateway("http://local", "bcp-test", tmp_path / "session")
    def response(*args, **kwargs):
        return io.BytesIO(json.dumps(health).encode())
    with patch("benchmarks.bcp.doc_labeling.common.urlopen", response):
        gateway.preflight(cfg)
        with pytest.raises(ValueError, match="sampling"):
            gateway.preflight({**cfg, "sampling": {**cfg["sampling"], "temperature": 0.1}})
        health["log_dir"] = "relative/logs"
        with pytest.raises(ValueError, match="absolute"):
            gateway.preflight(cfg)


def test_unknown_usage_prevents_savings_from_being_complete(pilot):
    folder, tokens, cfg = pilot
    chunks = rows(folder / "chunks.jsonl")
    pages = pack(chunks, tokens.count, 180)
    reply = tool_reply("answer")
    reply["usage"] = {"completion_tokens": 3}
    result = run_session("q", pages, chunks, cfg, tokens, lambda *a, **k: reply, condition="ranked")
    assert result["total_model_input_tokens"] is None
    assert result["known_total_model_input_tokens"] == 0 and not result["usage_complete"]


def test_source_token_coverage_does_not_double_count_boundary_tokens():
    tokens = Tokens.__new__(Tokens)
    tokens.tok = lambda text, **kwargs: {"offset_mapping": [(0, 3), (3, 6), (6, 9)]}
    assert tokens.coverage("abcdefghi", [(0, 2), (2, 4)]) == 2
    assert tokens.coverage("abcdefghi", [(0, 3), (6, 9)]) == 2
    assert tokens.coverage("abcdefghi", []) == 0
