"""Phase 0 analysis code: string grading, the offline order audit, and the judge templates (synthetic data only)."""

import hashlib
import json

from benchmarks.bcp import prompts
from benchmarks.bcp.doc_labeling.pages import pack, render
from benchmarks.bcp.doc_labeling.study2 import order_audit as oa
from benchmarks.bcp.doc_labeling.study2 import trials


def words(text):
    return len(text.split())


def test_grader_hashes_pin_both_templates():
    assert hashlib.sha256(prompts.GRADER_TEMPLATE_UPSTREAM.encode()).hexdigest() == prompts.GRADER_HASHES["upstream"]
    assert hashlib.sha256(prompts.GRADER_TEMPLATE.encode()).hexdigest() == prompts.GRADER_HASHES["legacy"]
    assert prompts.GRADER_TEMPLATE_UPSTREAM == prompts.GRADER_TEMPLATE_UPSTREAM.strip()
    assert "semantically equivalent" in prompts.GRADER_TEMPLATE_UPSTREAM


def test_string_grading_rules():
    assert trials.grade("Blue Whale", "a big blue whale!", "answered") == {
        "answered": True, "exact": False, "contains": True, "abstained_guess_matches": False}
    assert trials.grade("Blue Whale", "blue whale", "answered")["exact"]
    # An abstention is incorrect even when its guess matches; it is flagged.
    g = trials.grade("Blue Whale", "blue whale", "insufficient_evidence")
    assert not g["contains"] and not g["exact"] and g["abstained_guess_matches"]
    assert not trials.grade("Blue Whale", "", "answered")["contains"]


def test_records_cache_is_slim(tmp_path):
    data = tmp_path / "BrowseComp-Plus" / "data"
    data.mkdir(parents=True)
    rec = {"query_id": 7, "query": "q", "answer": "a", "gold_docs": [{"docid": "1", "text": "long"}],
           "evidence_docs": [{"docid": "2", "text": "long"}], "negative_docs": []}
    (data / "browsecomp_plus_decrypted.jsonl").write_text(json.dumps(rec) + "\n")
    out = trials.records(tmp_path)
    assert out == {"7": {"query": "q", "answer": "a", "gold_docs": ["1"], "evidence_docs": ["2"]}}
    assert "long" not in (tmp_path / "cache" / "records-slim.json").read_text()


def chunk(docid, i, text):
    return {"docid": docid, "chunk_id": f"s{i:03d}", "start": 0, "end": len(text), "text": text, "ordinal": i}


def test_parse_pages_round_trips_render():
    chunks = [chunk("10", 0, "alpha beta"), chunk("10", 1, "gamma\n\ndelta"), chunk("11", 2, "eps")]
    pages = pack(chunks, words, cap=12)
    back = oa.parse_pages(pages)
    assert [back[c["chunk_id"]]["text"] for c in chunks] == [c["text"] for c in chunks]
    assert {c["chunk_id"]: c["docid"] for c in back.values()} == {"s000": "10", "s001": "10", "s002": "11"}


def test_lead_first_takes_each_hits_first_chunk_first():
    chunks = [chunk("A", 0, "a0"), chunk("A", 1, "a1"), chunk("B", 2, "b0"), chunk("C", 3, "c0"), chunk("C", 4, "c1")]
    assert [c["chunk_id"] for c in oa.lead_first(chunks)] == ["s000", "s002", "s003", "s001", "s004"]


def test_audit_trial_compares_jev_page_one_with_free_orders(tmp_path):
    # Ten-word chunks, 25-word cap: two chunks per page (headers add 7 words each).
    text = lambda tag: " ".join([tag] * 10)
    chunks = [chunk("A", 0, text("a0")), chunk("A", 1, text("a1")), chunk("A", 2, "ANSWER " + text("a2")),
              chunk("G", 3, text("g0")), chunk("G", 4, text("g1"))]
    jev_order = [chunks[3], chunks[2], chunks[0], chunks[1], chunks[4]]  # Jev puts gold G first
    pages = pack(jev_order, words, cap=40)
    unit = tmp_path / "search" / "u1"
    unit.mkdir(parents=True)
    (unit / "pages.json").write_text(json.dumps(pages))
    trial = {"arm": "ranked_doc_search_labels", "trial_dir": tmp_path,
             "summary": {"answer": {"citations": ["G:s003", "A:s002", "A"]}}}
    record = {"answer": "answer", "gold_docs": ["G"], "evidence_docs": []}
    audit = oa.audit_trial(trial, record, words, cap=40)
    u = audit["search"][0]
    assert u["jev_p1"] == {"s003", "s002"}
    assert u["hit_p1"] == {"s000", "s001"}
    assert u["lead_p1"] == {"s000", "s003"}
    assert u["overlap_hit"] == 0 and u["overlap_lead"] == 0.5
    assert u["relevant"] == [{"docid": "G", "gold": True, "first": {"jev": True, "hit": False, "lead": True},
                              "any": {"jev": True, "hit": False, "lead": True}}]
    assert u["answer_chunks"] == {"s002"}
    cites = oa.citations(trial, audit)
    assert [c.get("only_jev") for c in cites] == [True, True, None]
    assert cites[2]["citation"] == "bare-docid"
    s = oa.summarize([audit])
    assert s["searches"] == 1 and s["relevant"] == 1 and s["first"]["jev"] == 1 and s["answer"]["lead"] == 0


def test_headroom_offsets_and_buckets():
    from benchmarks.bcp.doc_labeling.study2 import headroom as H

    class Tok:  # one token per word, with character offsets
        def __call__(self, text, add_special_tokens=False, return_offsets_mapping=True):
            import re
            return {"offset_mapping": [(m.start(), m.end()) for m in re.finditer(r"\S+", text)]}

    view = "Title: The Blue Whale\nIntro words here. Later the blue whale appears again, bluewhale too."
    assert H.first_offset(view, "Blue whale", Tok()) == 3      # "Blue" is the 3rd word ("The" is an article)
    assert H.first_offset(view, "whale shark", Tok()) is None
    assert H.first_offset("a1999b", "1999", Tok()) is None     # whole words only
    assert [H.bucket(x) for x in (1, 512, 513, 2000, 2001, 8192, None)] == [
        "snippet", "snippet", "page1", "page1", "deep", "deep", "absent"]


def test_regrade_parse_follows_upstream():
    from benchmarks.bcp.doc_labeling.study2 import regrade as R
    assert R.parse("extracted_final_answer: x\n**correct:** Yes\n**confidence:** 85%") == {
        "correct": True, "confidence": 85.0, "parse_error": False}
    assert R.parse("correct: no\nconfidence: 140")["confidence"] == 100.0
    assert R.parse("no verdict")["parse_error"]
    prompt = R.judge_prompt("Q?", "Exact Answer: A", "A")
    assert "[question]: Q?" in prompt and "[correct_answer]: A" in prompt
    assert R.SAMPLING == {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "max_tokens": 4096}
    assert R.ROUTES["vllm"]["thinking_off"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_trace_audit_helpers():
    from benchmarks.bcp.doc_labeling.study2 import trace_audit as A
    assert A.clm_command(json.dumps({"command": 'bcp_search "red fox habitat" | head -50'})) == ("search", "red fox habitat")
    assert A.clm_command(json.dumps({"command": "bcp_get_document 12345"})) == ("document", "12345")
    assert A.clm_command(json.dumps({"command": "cat /tmp/x"})) is None
    assert A._prefix_in("abcdef" * 100, "zz" + ("abcdef" * 100)[:300], step=1) == 300
    events = [{"kind": "document", "docids": ["9"], "returned": ["h1", "h2"], "admitted": ["h1"], "admitted_docs": ["9"]},
              {"kind": "document", "docids": ["9"], "returned": ["h1"], "admitted": ["h1"], "admitted_docs": ["9"]},
              {"kind": "search", "docids": ["9", "8"], "returned": [], "admitted": [], "admitted_docs": ["8"]}]
    r = A.repeats(events)
    assert (r["document_reopens"], r["chunks_returned_repeats"], r["chunks_admitted_repeats"]) == (1, 1, 1)
    assert A.recognition(events, {"gold_docs": ["9"]}) == {"gold_returned": True, "gold_on_page1": False,
                                                           "gold_opened": True}


def test_uniform_coverage_by_half_rule():
    from benchmarks.bcp.doc_labeling.study2 import trace_audit as A

    class Corpus:
        def search(self, q):
            return [{"docid": "5", "snippet": "one two three four five six seven eight"}]

        def document(self, d):
            return ""

    uni = A.Uniform.__new__(A.Uniform)
    uni.corpus, uni.snippets, uni.documents = Corpus(), {}, {}
    uni.count = lambda t: len(t.split())
    A.SNIPPET_CHUNK_SAVED = A.SNIPPET_CHUNK
    A.SNIPPET_CHUNK = 4
    try:
        uni.search("q")
        chunks = uni.chunks("5", "snippet")
        assert len(chunks) == 2
        first_len = chunks[0][1] - chunks[0][0]
        assert len(uni.covered("5", "snippet", [(0, first_len)])) == 1
        assert len(uni.covered("5", "snippet", [(0, 3)])) == 0
    finally:
        A.SNIPPET_CHUNK = A.SNIPPET_CHUNK_SAVED


def test_jev_dollars_use_billed_input_tokens_only():
    from benchmarks.bcp.doc_labeling.study2 import cost as C
    assert C.PRICE_PER_INPUT_TOKEN * 1_000_000 == 0.042
    rows = [{"study": "s2", "run": "q1-r1", "qid": "1", "arm": "full", "arm_id": "M0", "repeat": "1", "PF": 1.0,
             "jev_usd": 0.0},
            {"study": "s2", "run": "q1-r1", "qid": "1", "arm": "ranked_doc", "arm_id": "M1", "repeat": "1", "PF": 0.6,
             "jev_usd": 0.002},
            {"study": "s2", "run": "q1-r1", "qid": "1", "arm": "ranked_doc_search", "arm_id": "M2", "repeat": "1",
             "PF": 1.2, "jev_usd": 0.01}]
    be = {r["arm_id"]: r for r in C.break_even(rows)}
    assert abs(be["M1"]["usd_per_pflop"] - 0.005) < 1e-12
    assert be["M2"]["usd_per_pflop"] is None and be["M2"]["pflops_saved"] < 0
