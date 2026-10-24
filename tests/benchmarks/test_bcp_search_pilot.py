"""Synthetic full-search trials: no private benchmark text or live requests."""

import copy
import hashlib
import json
import sqlite3

import pytest

from benchmarks.bcp.doc_labeling import search_pilot
from benchmarks.bcp.doc_labeling.common import read, rows, settings
from benchmarks.bcp.doc_labeling.retrieval import Corpus
from benchmarks.bcp.doc_labeling.prepare import chunk_document
from benchmarks.bcp.doc_labeling.search_pilot import (RANKED_ARMS, canonical_citations, context_measures, judge,
                                                    ranked_doc_pages, ranked_pages, report, page_importance,
                                                    requested_topics, system_prompt, tools_for, trial)
from tests.benchmarks.test_bcp_doc_labeling import CharTokens, FakeJev, tool_reply

# sha256 of {"system", "tools"} for each baseline arm at the committed pilot (92b02ff): they must never change.
BASELINE = {"full": "b1e7facc01c253c4ab5ebe4bdc5b5949846a35592e279bfeda79d3f62f1ada68",
            "original": "bcf40ecde3ecdb35dbcf01a2999a37f6644338482ca87cfed0d6aa51484f0aa5",
            "ranked": "750623784c3e1af8d9817e2d2e766bcdae90e205c5c7be69794d9052a841d9c7"}


class FakeDocJev(FakeJev):
    """Scores a "<chunk_id>_<topic_id>" question by its chunk's text, the same rule as FakeJev."""

    def ask(self, state, questions):
        self.seen.append((copy.deepcopy(state), list(questions)))
        text = {c["chunk_id"]: c["text"] for c in state["document"]}
        return {k: {"noul": 0.9 if "evidence" in text[k.rsplit("_", 1)[0]] else 0.1} for k in questions}, {"input_tokens": 10}


class Search:
    def search(self, query):
        return [{"docid": "42", "snippet": "Evidence exists in this document"}]

    def document(self, docid):
        assert docid == "42"
        return "Background. " * 30 + "Important evidence happened in 1900."


def test_delivery_prompts_and_tools_are_arm_specific():
    full, ranked = system_prompt("full", 2000), system_prompt("ranked", 1800)
    assert "FULL-DOCUMENT mode" in full and "no subsequent pages" in full
    assert "TOPIC-RANKED PAGING mode" in ranked and "1800 text tokens" in ranked
    assert "NOT separate pages per topic" in ranked
    assert "not a guaranteed globally descending" in ranked
    assert "normally move to a different document" in ranked
    assert "does not prove later pages contain no evidence" in ranked
    assert "next_page" not in [t["function"]["name"] for t in tools_for("full")]
    assert "next_page" in [t["function"]["name"] for t in tools_for("ranked")]
    assert "no further pages" in tools_for("full")[1]["function"]["description"]
    assert "page 1" in tools_for("ranked")[1]["function"]["description"]
    assert tools_for("ranked")[1]["function"]["parameters"]["required"] == ["docid", "topics"]
    assert "no separate topic-generation call" in ranked
    assert "next_page_importance_percent" in ranked
    with pytest.raises(ValueError):
        system_prompt("unknown", 2000)
    assert "ORIGINAL-ORDER PAGING mode" in system_prompt("original", 2000)
    assert "topics" not in tools_for("original")[1]["function"]["parameters"]["required"]
    assert "next_page" in [t["function"]["name"] for t in tools_for("original")]


def test_canonical_search_citations():
    assert canonical_citations(["document 42 chunk c00001", "42"], {"42:c00001", "42"}) == ["42:c00001", "42"]
    with pytest.raises(ValueError):
        canonical_citations(["43:c00001"], {"42:c00001"})


def test_baseline_arms_are_unchanged_and_ranked_doc_matches_ranked():
    def fingerprint(arm):
        value = json.dumps({"system": system_prompt(arm, 2000), "tools": tools_for(arm)}, sort_keys=True)
        return hashlib.sha256(value.encode()).hexdigest()
    assert {arm: fingerprint(arm) for arm in BASELINE} == BASELINE
    assert fingerprint("ranked_doc") == BASELINE["ranked"]


@pytest.mark.parametrize("arm", ["full", "original", "ranked", "ranked_doc"])
def test_search_agent_opens_arbitrary_hit_and_answers(tmp_path, arm):
    cfg = settings({"chunk_tokens": 40, "page_tokens": 180, "context_tokens": 100000, "repeats": 1})
    class Gateway:
        def __init__(self):
            self.calls = []
        def call(self, messages, sampling, tools):
            self.calls.append(messages.copy())
            step = len(self.calls)
            if step == 1:
                return tool_reply("search", {"query": "evidence"})
            if step == 2:
                return tool_reply("get_document", {"docid": "42", **({"topics": ["when the event happened"]} if arm in RANKED_ARMS else {})})
            return tool_reply("answer", {"explanation": "Evidence", "exact_answer": "1900",
                "confidence": 90, "insufficient_evidence": False, "citations": ["42"]})
    gateway, jev = Gateway(), FakeDocJev() if arm == "ranked_doc" else FakeJev()
    result = trial(tmp_path, "When?", arm, Search(), CharTokens(), cfg, gateway, jev)
    assert result["status"] == "answered"
    assert result["tool_calls"] == {"search": 1, "get_document": 1, "answer": 1}
    assert result["documents_opened"] == ["42"]
    assert bool(jev.seen) == (arm in RANKED_ARMS)
    assert all("1900" not in m.get("content", "") for m in gateway.calls[0])
    assert gateway.calls[0][0]["content"] == system_prompt(arm, cfg["page_tokens"])
    assert len(rows(tmp_path / "tools.jsonl")) == 2
    assert result["document_tool_output_tokens"] > 0
    tool_rows = rows(tmp_path / "tools.jsonl")
    assert result["document_tool_output_tokens"] == tool_rows[1]["output_tokens"]
    assert tool_rows[1]["output_tokens"] == CharTokens().count(json.dumps(tool_rows[1]["result"], ensure_ascii=False))
    if arm in RANKED_ARMS:
        assert tool_rows[1]["result"]["topics"] == ["when the event happened"]
        assert "importance_percent" in tool_rows[1]["result"]
    assert trial(tmp_path, "When?", arm, Search(), CharTokens(), cfg, gateway, jev) == result


@pytest.mark.parametrize("value", [None, [], [""], ["a"] * 4, ["a", " a "], [5], ["a" * 241]])
def test_agent_topics_are_required_short_and_distinct(value):
    with pytest.raises(ValueError):
        requested_topics(value)


def test_page_importance_uses_maxima_not_fabricated_union_probabilities():
    pages = [{"chunk_ids": ["a", "b"]}, {"chunk_ids": ["c"]}]
    topics = requested_topics(["date", "identity"])
    scores = {"a": {"t1": .9, "t2": .2}, "b": {"t1": .4, "t2": .8}, "c": {"t1": .3, "t2": .5}}
    page_importance(pages, scores, topics)
    assert pages[0]["importance_percent"] == 90
    assert pages[0]["importance_by_topic_percent"] == [90, 80]
    assert pages[0]["next_page_importance_percent"] == 50
    assert pages[1]["next_page_importance_percent"] is None
    assert pages[1]["next_page_importance_by_topic_percent"] is None


def test_topic_changes_reset_page_and_reopening_reuses_jev_scores(tmp_path):
    cfg = settings({"chunk_tokens": 40, "page_tokens": 180, "context_tokens": 100000})
    actions = [("search", {"query": "evidence"}),
               ("get_document", {"docid": "42", "topics": ["date"]}),
               ("next_page", {"docid": "42"}),
               ("get_document", {"docid": "42", "topics": ["identity"]}),
               ("get_document", {"docid": "42", "topics": ["date"]}),
               ("answer", {"explanation": "Evidence", "exact_answer": "1900", "confidence": 90,
                           "insufficient_evidence": False, "citations": ["42"]})]
    class Gateway:
        def call(self, *args, **kwargs):
            name, arguments = actions.pop(0)
            return tool_reply(name, arguments)
    jev = FakeJev()
    result = trial(tmp_path, "When?", "ranked", Search(), CharTokens(), cfg, Gateway(), jev)
    trace = rows(tmp_path / "tools.jsonl")
    assert result["status"] == "answered"
    assert [row["result"]["page"] for row in trace[1:]] == [1, 2, 1, 1]
    assert trace[1]["result"]["next_page_importance_percent"] == trace[2]["result"]["importance_percent"]
    assert trace[2]["result"]["topics"] == ["date"]
    assert len(list((tmp_path / "documents" / "42").iterdir())) == 2
    assert len(jev.seen) == 2 * len(chunk_document(Search().document("42"), "42", CharTokens().count, cfg["chunk_tokens"]))


def test_doc_ranking_scores_once_and_preserves_all_chunks(tmp_path):
    cfg = settings({"chunk_tokens": 40, "page_tokens": 180})
    jev, tokens = FakeJev(), CharTokens()
    topics = [{"id": "t1", "name": "date", "description": "when"}]
    text = Search().document("42")
    chunks, pages = ranked_pages(tmp_path, "When?", topics, "42", text, tokens, cfg, jev)
    calls = len(jev.seen)
    assert "".join(c["text"] for c in chunks) == text
    assert len({c for p in pages for c in p["chunk_ids"]}) == len(chunks)
    ranked_pages(tmp_path, "When?", topics, "42", text, tokens, cfg, jev)
    assert len(jev.seen) == calls
    assert rows(tmp_path / "jev.jsonl")[0]["answers"]


def test_ranked_doc_scores_whole_document_in_one_request(tmp_path):
    cfg, tokens = settings({"chunk_tokens": 40, "page_tokens": 180}), CharTokens()
    topics = requested_topics(["date", "identity"])
    text, jev = Search().document("42"), FakeDocJev()
    chunks, pages = ranked_doc_pages(tmp_path / "doc", "When?", topics, "42", text, tokens, cfg, jev)
    assert len(jev.seen) == 1
    state, keys = jev.seen[0]
    assert [c["text"] for c in state["document"]] == [c["text"] for c in chunks]
    assert state["question"] == "When?" and state["topics"] == topics
    assert sorted(keys) == sorted(f"{c['chunk_id']}_{t['id']}" for c in chunks for t in topics)
    # Same scores, same pages: only how Jev is asked differs from ranked.
    _, same = ranked_pages(tmp_path / "chunk", "When?", topics, "42", text, tokens, cfg, FakeJev())
    assert pages == same
    ranked_doc_pages(tmp_path / "doc", "When?", topics, "42", text, tokens, cfg, jev)
    assert len(jev.seen) == 1                      # resumed from its own audit log, not rescored


def test_ranked_doc_splits_questions_but_keeps_the_whole_state(tmp_path, monkeypatch):
    cfg, tokens = settings({"chunk_tokens": 40, "page_tokens": 180}), CharTokens()
    topics = requested_topics(["date", "identity", "place"])
    text, jev = Search().document("42"), FakeDocJev()
    state_size = len(json.dumps({"question": "When?", "topics": topics, "document": [
        {"chunk_id": c["chunk_id"], "text": c["text"]} for c in chunk_document(text, "42", tokens.count, 40)]}))
    monkeypatch.setattr(search_pilot, "JEV_REQUEST_CHARS", state_size + 2000)
    chunks, _ = ranked_doc_pages(tmp_path, "When?", topics, "42", text, tokens, cfg, jev)
    assert len(jev.seen) > 1
    assert all(state == jev.seen[0][0] for state, _ in jev.seen)
    asked = [k for _, keys in jev.seen for k in keys]
    assert sorted(asked) == sorted(f"{c['chunk_id']}_{t['id']}" for c in chunks for t in topics)
    monkeypatch.setattr(search_pilot, "JEV_QUESTION_CHARS", state_size)
    with pytest.raises(ValueError, match="per-question limit"):
        ranked_doc_pages(tmp_path / "too-big", "When?", topics, "42", text, tokens, cfg, jev)


@pytest.mark.parametrize("bad", ["missing", 1.5, float("nan"), "0.5"])
def test_ranked_doc_rejects_missing_or_invalid_scores(tmp_path, bad):
    class BadJev(FakeDocJev):
        def ask(self, state, questions):
            answers, usage = super().ask(state, questions)
            first = next(iter(answers))
            if bad == "missing":
                del answers[first]
            else:
                answers[first] = {"noul": bad}
            return answers, usage
    cfg = settings({"chunk_tokens": 40, "page_tokens": 180})
    with pytest.raises((ValueError, KeyError)):
        ranked_doc_pages(tmp_path, "When?", requested_topics(["date"]), "42", Search().document("42"),
                         CharTokens(), cfg, BadJev())
    assert rows(tmp_path / "jev.jsonl")[-1]["status"] == "failed"
    assert not (tmp_path / "pages.json").exists()


def test_context_measures_peak_and_gold_document_luck(tmp_path):
    with (tmp_path / "calls.jsonl").open("w") as stream:
        for prompt in (100, 300, 200):
            stream.write(json.dumps({"response": {"usage": {"prompt_tokens": prompt, "completion_tokens": 5}}}) + "\n")
    with (tmp_path / "tools.jsonl").open("w") as stream:
        for row in ({"step": 1, "tool": "search", "result": [{"docid": "1"}]},
                    {"step": 2, "error": "malformed"},
                    {"step": 3, "tool": "search", "result": [{"docid": "9"}, {"docid": "2"}]},
                    {"step": 4, "tool": "get_document", "arguments": {"docid": "1"}},
                    {"step": 5, "tool": "get_document", "arguments": {"docid": "9"}}):
            stream.write(json.dumps(row) + "\n")
    result = {"tool_calls": {"search": 2, "get_document": 2, "next_page": 1}, "documents_opened": ["1", "9"],
              "document_tool_output_tokens": 600}
    measures = context_measures(tmp_path, result, {"9"})
    assert measures["peak_context_tokens"] == 300
    assert (measures["search_calls"], measures["next_page_calls"], measures["documents_opened_count"]) == (2, 1, 2)
    assert measures["document_tokens_per_opened_document"] == 300
    assert (measures["gold_doc_in_search"], measures["first_gold_search_step"]) == (True, 3)
    assert (measures["gold_doc_opened"], measures["first_gold_open_step"]) == (True, 5)
    assert context_measures(tmp_path, result, set())["gold_doc_in_search"] is None
    evidence = context_measures(tmp_path, result, {"9"}, {"1", "9"})
    assert (evidence["evidence_doc_in_search"], evidence["first_evidence_search_step"]) == (True, 1)
    assert (evidence["evidence_doc_opened"], evidence["first_evidence_open_step"]) == (True, 4)
    assert evidence["gold_doc_opened"] is True and evidence["first_gold_open_step"] == 5
    (tmp_path / "calls.jsonl").write_text(json.dumps({"response": {}}) + "\n")
    assert context_measures(tmp_path, result, {"9"})["peak_context_tokens"] is None


def test_judge_grades_answers_once_and_only_answers(tmp_path, monkeypatch):
    out = tmp_path / "q741-r1"
    (out / "741").mkdir(parents=True)
    (out / "manifest.json").write_text(json.dumps({"query_ids": ["741"], "arms": ["full", "original", "ranked"]}))
    queries = tmp_path / "queries.jsonl"
    queries.write_text(json.dumps({"query_id": "741", "query": "Who?", "answer": "Ada",
                                   "gold_docs": [{"docid": "42"}]}) + "\n")
    answer = {"explanation": "It says so.", "exact_answer": "Ada Lovelace", "confidence": 90,
              "citations": ["42"], "insufficient_evidence": False}
    for arm, status in (("full", "answered"), ("original", "context_limit"), ("ranked", "insufficient_evidence")):
        (out / "741" / arm).mkdir()
        (out / "741" / arm / "result.json").write_text(json.dumps({"arm": arm, "status": status,
            "answer": answer if status == "answered" else None,
            "total_model_input_tokens": 10, "total_model_output_tokens": 1}))
    seen = []
    class FakeGateway:
        def __init__(self, url, run_id, folder):
            self.run_id = run_id
        def call(self, messages, sampling, kind):
            seen.append((self.run_id, messages[0]["content"], sampling["model"], kind))
            return {"choices": [{"message": {"content": "<think>hm</think>correct: yes"}}]}
    monkeypatch.setattr(search_pilot, "Gateway", FakeGateway)
    judge(out, queries, "http://judge")
    judge(out, queries, "http://judge")
    assert len(seen) == 1
    run_id, prompt, model, kind = seen[0]
    assert run_id == "bcp-search.q741-r1.741.full.judge" and kind == "judge" and model == "qwen3:32b"
    assert "Ada Lovelace" in prompt and "Ada" in prompt
    assert read(out / "741/full/verdict.json")["correct"] is True
    assert read(out / "741/ranked/verdict.json")["correct"] is False
    assert not (out / "741/original/verdict.json").exists()
    data = report(out, queries, CharTokens())
    verdicts = {r["arm"]: (r["judged_correct"], r["judge_parsed"]) for r in data["runs"]}
    assert verdicts == {"full": (True, True), "original": (None, False), "ranked": (False, True)}


def test_flop_input_drops_only_the_schema_keyword_purdue_drops():
    tools = tools_for("ranked")
    stripped = search_pilot.server_tools(tools)
    assert "additionalProperties" not in json.dumps(stripped)
    assert json.dumps(stripped).replace(" ", "") in json.dumps(tools).replace(', "additionalProperties": false', "").replace(" ", "")
    assert [t["function"]["name"] for t in stripped] == [t["function"]["name"] for t in tools]
    assert tools_for("ranked") == tools                     # the agent's own tools are untouched


def test_retrieval_ignores_query_sql_and_searches_whole_collection(tmp_path):
    path = tmp_path / "corpus.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE documents(docid TEXT,text TEXT,url TEXT)")
        db.executemany("INSERT INTO documents VALUES (?,?,?)", [("42", "important evidence", "a"), ("43", "unrelated", "b")])
        db.execute("CREATE VIRTUAL TABLE search USING fts5(text,content='documents',content_rowid='rowid')")
        db.execute("INSERT INTO search(search) VALUES ('rebuild')")
        db.execute("CREATE TABLE metadata(value TEXT)")
        db.execute("INSERT INTO metadata VALUES (?)", (json.dumps({"documents": 100195}),))
    corpus = Corpus(path, CharTokens())
    corpus.cut = lambda text, limit: text
    assert corpus.search('evidence " OR DROP TABLE documents;')[0]["docid"] == "42"
    assert corpus.document("43") == "unrelated"
    assert corpus.search("!!!") == []
    corpus.db.close()


def test_token_report_does_not_run_flops_and_counts_topic_overhead(tmp_path, monkeypatch):
    def forbidden(*args):
        raise AssertionError("FLOP analysis must be opt-in")
    monkeypatch.setattr("benchmarks.bcp.doc_labeling.search_pilot.measure", forbidden)
    queries = tmp_path / "queries.jsonl"
    queries.write_text("\n".join(json.dumps({"query_id": q, "answer": "1900"}) for q in ("741", "333", "600")))
    for arm, inp, output in (("full", 100, 10), ("ranked", 60, 20)):
        folder = tmp_path / "741" / arm
        folder.mkdir(parents=True)
        (folder / "result.json").write_text(json.dumps({"arm": arm, "answer": {"exact_answer": "1900"},
            "status": "answered", "total_model_input_tokens": inp, "total_model_output_tokens": output,
            "document_tool_output_tokens": 80 if arm == "full" else 30, "search_tool_output_tokens": 10}))
    topic_dir = tmp_path / "741/topics-generation"
    topic_dir.mkdir()
    (topic_dir / "calls.jsonl").write_text(json.dumps({"response": {"usage": {"prompt_tokens": 20, "completion_tokens": 10}}}) + "\n")
    data = report(tmp_path, queries, CharTokens())
    ranked = next(r for r in data["runs"] if r["arm"] == "ranked")
    assert data["flops_analysis_requested"] is False
    assert ranked["model_flops"] is None
    assert ranked["answerer_input_saved_vs_full"] == 40
    assert ranked["input_saved_vs_full_including_topics"] == 20
    assert ranked["output_saved_vs_full_including_topics"] == -20
    assert ranked["whole_system_flops_complete"] is False
    assert ranked["document_tool_output_tokens_saved_vs_full"] == 50
    assert ranked["document_tool_output_tokens_reduction_vs_full"] == .625
    assert ranked["search_tool_output_tokens_saved_vs_full"] == 0


def test_three_way_report_compares_original_and_total_tokens(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"query_ids": ["741"], "arms": ["full", "original", "ranked"]}))
    queries = tmp_path / "queries.jsonl"
    queries.write_text(json.dumps({"query_id": "741", "answer": "1900"}) + "\n")
    for arm, inp, output, document in (("full", 100, 10, 80), ("original", 150, 20, 90), ("ranked", 80, 15, 30)):
        folder = tmp_path / "741" / arm
        folder.mkdir(parents=True)
        (folder / "result.json").write_text(json.dumps({"answer": {"exact_answer": "1900"},
            "status": "answered", "arm": arm, "total_model_input_tokens": inp,
            "total_model_output_tokens": output, "document_tool_output_tokens": document}))
    data = report(tmp_path, queries, CharTokens())
    assert len(data["runs"]) == 3
    ranked = next(r for r in data["runs"] if r["arm"] == "ranked")
    assert ranked["total_model_tokens"] == 95
    assert ranked["total_tokens_saved_vs_full"] == 15
    assert ranked["total_tokens_saved_vs_original"] == 75
    assert ranked["document_tool_output_tokens_saved_vs_original"] == 60
