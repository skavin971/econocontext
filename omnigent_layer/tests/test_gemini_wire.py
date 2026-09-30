"""Gemini's generateContent format: paths, usage, and what a call carried."""

import json
from pathlib import Path

from omnigent_layer import gemini_wire


def test_model_calls_are_read_from_both_path_shapes():
    assert gemini_wire.model_call("/v1beta1/publishers/google/models/gemini-3.6-flash"
                                  ":streamGenerateContent") == ("gemini-3.6-flash",
                                                                "streamGenerateContent")
    assert gemini_wire.model_call("/v1beta/models/gemini-3.6-flash:generateContent?alt=sse") == \
        ("gemini-3.6-flash", "generateContent")
    assert gemini_wire.model_call("/v1beta1/projects/p/locations/global/publishers/google/"
                                  "models/m:countTokens") == ("m", "countTokens")
    assert gemini_wire.model_call("/v1beta1/something") == (None, None)


def test_usage_from_the_vertex_probe():
    # The real reply of 2026-09-30: thoughts outside candidates, nothing cached.
    usage = gemini_wire.to_usage({"promptTokenCount": 7, "candidatesTokenCount": 1,
                                  "totalTokenCount": 99, "thoughtsTokenCount": 91})
    assert (usage.uncached_input, usage.cache_read, usage.output, usage.reasoning) == (7, 0, 92, 91)
    assert usage.cache_write is None and not usage.cache_write_applicable


def test_cached_and_tool_prompt_tokens():
    usage = gemini_wire.to_usage({"promptTokenCount": 1000, "toolUsePromptTokenCount": 50,
                                  "cachedContentTokenCount": 600, "candidatesTokenCount": 20,
                                  "totalTokenCount": 1070})
    assert (usage.uncached_input, usage.cache_read, usage.output, usage.reasoning) == (450, 600, 20, 0)


def test_missing_counts_are_unknown_unless_the_total_confirms_zero():
    confirmed = gemini_wire.to_usage({"promptTokenCount": 10, "candidatesTokenCount": 5,
                                      "totalTokenCount": 15})
    assert confirmed.output == 5 and confirmed.reasoning == 0
    unconfirmed = gemini_wire.to_usage({"promptTokenCount": 10, "candidatesTokenCount": 5})
    assert unconfirmed.output is None
    nothing = gemini_wire.to_usage(None)
    assert nothing.uncached_input is None and nothing.output is None


def test_the_last_complete_usage_of_a_stream_wins():
    chunks = [{"usageMetadata": {"trafficType": "ON_DEMAND"}},
              {"usageMetadata": {"promptTokenCount": 3, "totalTokenCount": 3}}, {}, "junk"]
    assert gemini_wire.extract_usage(chunks) == {"promptTokenCount": 3, "totalTokenCount": 3}
    assert gemini_wire.extract_usage([]) is None


FIXTURE = Path(__file__).parent / "fixtures" / "gemini" / "smoke_last_request.json"


def call(name, args, id=None):
    return {"functionCall": {"name": name, "args": args, **({"id": id} if id else {})}}


def result(name, output, id=None):
    return {"functionResponse": {"name": name, "response": {"output": output},
                                 **({"id": id} if id else {})}}


def request(*contents, system="s"):
    return {"systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": "task"}]}, *contents]}


def test_the_latest_tool_results_of_a_real_request():
    # Gemini CLI's 6th request of the smoke task (trimmed): the last model turn asked
    # for update_topic only.
    body = json.loads(FIXTURE.read_text())
    latest = gemini_wire.find_latest_tool_result(body)
    assert [r["name"] for r in latest] == ["update_topic"]
    names = [f["name"] for f in gemini_wire.extract_function_responses(body)]
    assert names == ["update_topic", "read_file", "glob", "replace", "run_shell_command",
                     "update_topic"]


def test_results_pair_with_their_calls_by_id_else_by_position():
    by_id = request({"role": "model", "parts": [call("read_file", {"file_path": "a.py"}, "1"),
                                                 call("read_file", {"file_path": "b.py"}, "2")]},
                    {"role": "user", "parts": [result("read_file", "B", "2"),
                                               result("read_file", "A", "1")]})
    assert [(r["args"]["file_path"], r["response"]["output"])
            for r in gemini_wire.find_latest_tool_result(by_id)] == [("b.py", "B"), ("a.py", "A")]
    by_position = request({"role": "model", "parts": [call("glob", {"pattern": "*"}),
                                                       call("read_file", {"file_path": "a.py"})]},
                          {"role": "user", "parts": [result("glob", "a.py"), result("read_file", "A")]})
    assert [r["args"] for r in gemini_wire.find_latest_tool_result(by_position)] == \
        [{"pattern": "*"}, {"file_path": "a.py"}]
    assert gemini_wire.find_latest_tool_result(request()) == []  # the first call has none


def test_a_reply_is_read_for_its_calls_and_text_but_thoughts_are_not_text():
    chunks = [{"candidates": [{"content": {"parts": [{"text": "planning...", "thought": True}]}}]},
              {"candidates": [{"content": {"parts": [call("read_file", {"file_path": "a.py"}),
                                                     call("update_topic", {"title": "t"})]}}]}]
    seen = gemini_wire.observe_response(chunks)
    assert [(c["name"], c["kind"]) for c in seen["response_calls"]] == \
        [("read_file", "file"), ("update_topic", "meta")]
    assert seen["response_text"] is False
    chunks.append({"candidates": [{"content": {"parts": [{"text": "Done."}]}}]})
    assert gemini_wire.observe_response(chunks)["response_text"] is True


def test_tools_are_classified_and_unknown_tools_are_never_safe_reads():
    assert gemini_wire.tool_kind("grep_search") == "search"
    assert gemini_wire.tool_kind("run_shell_command") == "write"
    assert gemini_wire.tool_kind("invoke_agent") == gemini_wire.tool_kind("new_tool") == "other"
    assert gemini_wire.tool_path("replace", {"file_path": "a.py"}) == "a.py"
    assert gemini_wire.tool_path("glob", {"pattern": "*"}) is None


def test_a_sub_agent_request_has_its_own_context_key():
    root, child = request(), request(system="You are codebase_investigator")
    assert gemini_wire.context_key(root) != gemini_wire.context_key(child)
    longer = request({"role": "model", "parts": [call("glob", {"pattern": "*"})]})
    assert gemini_wire.context_key(root) == gemini_wire.context_key(longer)


def test_a_malformed_request_is_observed_as_empty_not_an_error():
    for body in ({}, {"contents": "x"}, {"contents": [1, {"parts": "y"}]}):
        seen = gemini_wire.observe_request(body, b"{}")
        assert seen["contents"] <= 1 and seen["function_responses"] == []
