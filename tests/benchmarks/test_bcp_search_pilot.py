"""Synthetic full-search trials: no private benchmark text or live requests."""

import json
import sqlite3

import pytest

from benchmarks.bcp.doc_labeling.common import rows, settings
from benchmarks.bcp.doc_labeling.retrieval import Corpus
from benchmarks.bcp.doc_labeling.prepare import chunk_document
from benchmarks.bcp.doc_labeling.search_pilot import (canonical_citations, ranked_pages, report,
                                                    page_importance, requested_topics, system_prompt, tools_for, trial)
from tests.benchmarks.test_bcp_doc_labeling import CharTokens, FakeJev, tool_reply


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


def test_canonical_search_citations():
    assert canonical_citations(["document 42 chunk c00001", "42"], {"42:c00001", "42"}) == ["42:c00001", "42"]
    with pytest.raises(ValueError):
        canonical_citations(["43:c00001"], {"42:c00001"})


@pytest.mark.parametrize("arm", ["full", "ranked"])
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
                return tool_reply("get_document", {"docid": "42", **({"topics": ["when the event happened"]} if arm == "ranked" else {})})
            return tool_reply("answer", {"explanation": "Evidence", "exact_answer": "1900",
                "confidence": 90, "insufficient_evidence": False, "citations": ["42"]})
    gateway, jev = Gateway(), FakeJev()
    result = trial(tmp_path, "When?", arm, Search(), CharTokens(), cfg, gateway, jev)
    assert result["status"] == "answered"
    assert result["tool_calls"] == {"search": 1, "get_document": 1, "answer": 1}
    assert result["documents_opened"] == ["42"]
    assert bool(jev.seen) == (arm == "ranked")
    assert all("1900" not in m.get("content", "") for m in gateway.calls[0])
    assert gateway.calls[0][0]["content"] == system_prompt(arm, cfg["page_tokens"])
    assert len(rows(tmp_path / "tools.jsonl")) == 2
    assert result["document_tool_output_tokens"] > 0
    tool_rows = rows(tmp_path / "tools.jsonl")
    assert result["document_tool_output_tokens"] == tool_rows[1]["output_tokens"]
    assert tool_rows[1]["output_tokens"] == CharTokens().count(json.dumps(tool_rows[1]["result"], ensure_ascii=False))
    if arm == "ranked":
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
