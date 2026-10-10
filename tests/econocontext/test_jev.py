"""Jev's request budget (econocontext/predictor/jev.py): measured in Jev's own units (2.5 characters per
Jev token; 28,000 Jev tokens = 70,000 characters, state and questions together), and its error
messages (Jev's error type only: no key, no content). No network: urlopen is replaced."""

import io
import json
from urllib.error import HTTPError

import pytest

from econocontext.predictor import jev as jev_module
from econocontext.predictor.jev import CHARS_PER_JEV_TOKEN, Jev

LIMIT = int(28_000 * CHARS_PER_JEV_TOKEN)                     # 70,000 characters
QUESTIONS = {"segment_done": {"type": "noul", "instructions": "Has the agent finished a part of the work?"}}


def request_size(state, questions=QUESTIONS):
    return len(json.dumps(state)) + len(json.dumps(questions))


def test_the_budget_is_28000_jev_tokens_of_2_5_characters():
    assert CHARS_PER_JEV_TOKEN == 2.5 and LIMIT == 70_000


def test_a_state_at_the_limit_fits_unchanged():
    state = {"task": "t", "conversation": [{"turn": 0, "text": ""}]}
    state["conversation"][0]["text"] = "x" * (LIMIT - request_size(state))
    assert request_size(state) == LIMIT
    before = json.loads(json.dumps(state))
    assert Jev().fit(state, QUESTIONS) == before and request_size(state) == LIMIT


def test_an_over_limit_state_is_cut_to_fit():
    # About 3x the limit: 60 turns of 2,000 characters and a tool result of 2,000 numbered lines.
    state = {"task": "t", "conversation": [{"turn": i, "text": "y" * 2000} for i in range(60)],
             "new_tool_result": {"tool": "bash", "lines": "\n".join(f"{i}\t" + "z" * 80 for i in range(1, 2001))}}
    assert request_size(state) > 3 * LIMIT
    fitted = Jev().fit(state, QUESTIONS)
    assert request_size(fitted) <= LIMIT
    assert [t["turn"] for t in fitted["conversation"]][-2:] == [58, 59]          # the most recent turns stay
    lines = fitted["new_tool_result"]["lines"].split("\n")
    assert lines[0].startswith("1\t") and lines[-1].startswith("2000\t") and "lines cut to fit" in "\n".join(lines)


def test_huge_recent_turns_and_tool_call_arguments_are_shortened_too():
    state = {"task": "t", "conversation": [
        {"turn": 1, "text": "a" * 90_000, "tool_calls": [{"name": "write_file", "arguments": "b" * 90_000}]},
        {"turn": 2, "text": "c" * 50_000}]}
    fitted = Jev().fit(state, QUESTIONS)
    assert request_size(fitted) <= LIMIT and [t["turn"] for t in fitted["conversation"]] == [1, 2]
    assert "characters cut to fit" in fitted["conversation"][0]["text"]


def answer_with_error(monkeypatch, body: bytes, code=400):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-jev-key-123456")

    def refuse(request, timeout=None):
        raise HTTPError(request.full_url, code, "Bad Request", {}, io.BytesIO(body))
    monkeypatch.setattr(jev_module, "urlopen", refuse)


def test_the_error_keeps_jevs_error_type_and_nothing_else(monkeypatch):
    answer_with_error(monkeypatch, json.dumps({"detail": {"error_type": "max_tokens_exceeded",
                                                          "message": "secret prompt text"}}).encode())
    with pytest.raises(RuntimeError) as caught:
        Jev().ask({"task": "t"}, QUESTIONS)
    assert str(caught.value) == "Jev HTTP 400 max_tokens_exceeded"
    assert "secret" not in str(caught.value) and "test-jev-key" not in str(caught.value)


def test_an_error_type_that_is_not_an_identifier_is_left_out(monkeypatch):
    answer_with_error(monkeypatch, json.dumps({"detail": {"error_type": "the prompt was: secret text"}}).encode())
    with pytest.raises(RuntimeError, match=r"^Jev HTTP 400$"):
        Jev().ask({"task": "t"}, QUESTIONS)
    answer_with_error(monkeypatch, b"<html>not json</html>", code=502)
    with pytest.raises(RuntimeError, match=r"^Jev HTTP 502$"):
        Jev().ask({"task": "t"}, QUESTIONS)
