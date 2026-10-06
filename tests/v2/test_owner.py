"""EconoContext owning the conversation (econocontext/owner.py): each rule fires when it should
and not when it must not. Fake predictor and fake summary call; no network."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from econocontext.owner import NOTE, Owner

BIG = "\n".join(f"row {i}: " + "x" * 60 for i in range(1, 301))       # ~5,000 tokens


class FakePredictor:
    name = "fake"

    def __init__(self, needed=0.1, done=0.9, segment_done=0.9, end=None):
        self.needed, self.done, self.segment_done, self.end = needed, done, segment_done, end
        self.asked = []

    def arrival(self, task, convo, item):
        self.asked.append("arrival")
        return {"needed_again": self.needed, "lifetime": "few_calls", "relevant": None}, {"input_tokens": 7}

    def segment(self, task, turns, items, boundaries):
        self.asked.append("segment")
        return {"done": {i["id"]: self.done for i in items}, "segment_done": self.segment_done,
                "segment_end": self.end if self.end is not None else (boundaries[-1] if boundaries else None)}, \
            {"input_tokens": 11}


def summary_call(text="Found the bug in app.py line 3; fixed it; tests pass."):
    sent = []

    async def complete(request):
        sent.append(request)
        usage = SimpleNamespace(model_dump=lambda: {"completion_tokens": 120})
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))], usage=usage)
    complete.sent = sent
    return complete


@pytest.fixture
def owner(tmp_path):
    count = iter(range(100))

    def build(predictor=None, force=(), cache=None):
        o = Owner("r1", "jev", tmp_path / f"s{next(count)}.sqlite3", force=force, cache=cache)
        o.predictor = predictor or FakePredictor()
        o.start("Fix the bug.")
        return o
    return build


def rules(o):
    return [(d["rule"], d["action"]) for d in o.s.decisions()]


def numbered(start, end, text=lambda n: f"line {n}"):
    body = [f"{n}\t{text(n)}" for n in range(start, end + 1)]
    return "\n".join(body + [f"[lines {start}-{end} of 300]", "(exit code 0)"])


# Rules 1, 3, 9 -------------------------------------------------------------------------
def test_an_identical_read_only_call_gets_a_note_while_the_copy_is_in_context(owner):
    o = owner()
    out = "\n".join(f"file{i}.py" for i in range(200))
    assert o.before_tool("bash", {"command": "ls -la"}) is None
    o.after_tool("bash", {"command": "ls -la"}, out, False, "c1")
    note = o.before_tool("bash", {"command": "ls -la"})
    assert note.startswith(NOTE + "Same output") and rules(o)[-1] == ("1 dont_repeat", "note")
    o.after_tool("bash", {"command": "ls -la"}, note, True, "c2")
    assert o.s.last_same("main", "bash", {"command": "ls -la"})["form"] == "note"


def test_after_the_copy_left_the_context_the_saved_output_is_served(owner):
    o = owner()
    o.after_tool("read_file", {"path": "a.py"}, numbered(1, 50), False, "c1")
    o.s.drop_from_context("main", set())
    served = o.before_tool("read_file", {"path": "a.py"})
    assert served == numbered(1, 50)                 # exactly what running it would return
    assert rules(o)[-1] == ("3 serve_stored", "serve")


def test_nothing_is_served_after_a_write_and_writes_bump_the_epoch(owner):
    o = owner()
    o.after_tool("bash", {"command": "cat a.py"}, "x", False, "c1")
    o.after_tool("write_file", {"path": "a.py", "content": "y"}, "wrote 1 bytes", False, "c2")
    o.after_tool("bash", {"command": "python fix.py"}, "ok", False, "c3")
    assert o.s.epoch == 2 and rules(o).count(("9 invalidate", "bump")) == 2
    assert o.before_tool("bash", {"command": "cat a.py"}) is None
    assert o.before_tool("bash", {"command": "python fix.py"}) is None     # never repeat a writing command


# Rule 2 --------------------------------------------------------------------------------
def test_an_overlapping_read_shows_only_new_lines_and_keeps_the_footer(owner):
    o = owner()
    o.after_tool("read_file", {"path": "a.py", "start_line": 1, "end_line": 50}, numbered(1, 50), False, "c1")
    shown = o.after_tool("read_file", {"path": "a.py", "start_line": 40, "end_line": 100},
                         numbered(40, 100), False, "c2")
    assert shown.startswith(NOTE + "Lines 40-50 are unchanged") and "\n51\tline 51" in shown
    assert "40\tline 40" not in shown and shown.endswith("[lines 40-100 of 300]\n(exit code 0)")


def test_a_changed_overlap_is_shown_in_full(owner):
    o = owner()
    o.after_tool("read_file", {"path": "a.py", "start_line": 1, "end_line": 50}, numbered(1, 50), False, "c1")
    out = numbered(40, 100, text=lambda n: f"changed {n}")
    assert o.after_tool("read_file", {"path": "a.py", "start_line": 40, "end_line": 100}, out, False, "c2") == out


# Rule 4 --------------------------------------------------------------------------------
def test_a_big_output_unlikely_to_be_needed_becomes_a_preview(owner):
    o = owner(FakePredictor(needed=0.05))
    shown = o.after_tool("bash", {"command": "pytest -q"}, BIG, False, "c1")
    assert shown.startswith(NOTE + "This output has 300 lines") and "lines not shown" in shown
    assert rules(o)[-1] == ("4 arrival", "preview")


def test_a_big_output_needed_soon_stays_full_unless_forced(owner):
    assert owner(FakePredictor(needed=0.95)).after_tool("bash", {"command": "pytest -q"}, BIG, False, "c1") == BIG
    forced = owner(FakePredictor(needed=0.95), force=["4 arrival"])
    assert "lines not shown" in forced.after_tool("bash", {"command": "pytest -q"}, BIG, False, "c1")


# Rules 6 and 7 ----------------------------------------------------------------------------
def conversation(o, prompt, calls=10):
    """system, task, then a big read (finished) and a small command, bound to the owner."""
    o.after_tool("bash", {"command": "cat log.txt"}, BIG, False, "c1")
    o.s.update_item(1, form="full", shown_tokens=5000)
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "Fix the bug."},
                {"role": "assistant", "tool_calls": [{"id": "c1", "function": {"name": "bash", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": "c1", "content": BIG},
                {"role": "assistant", "tool_calls": [{"id": "c2", "function": {"name": "bash", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": "c2", "content": "ok"}]
    usage = [{"usage": {"prompt_tokens": prompt}} for _ in range(calls)]
    return messages, usage


def test_a_finished_big_item_is_evicted_when_it_pays(owner):
    o = owner(FakePredictor(done=0.9, segment_done=0.1))
    messages, usage = conversation(o, prompt=12_000, calls=5)
    asyncio.run(o.before_call(messages, usage, summary_call()))
    assert messages[3]["content"].startswith(NOTE + "The output of this call") and rules(o)[-1] == ("6 evict", "evict")
    assert o.s.item(1)["in_context"] == 0


def test_an_unfinished_item_is_not_evicted(owner):
    o = owner(FakePredictor(done=0.1, segment_done=0.1))
    messages, usage = conversation(o, prompt=12_000, calls=5)
    asyncio.run(o.before_call(messages, usage, summary_call()))
    assert messages[3]["content"] == BIG and rules(o)[-1] == ("6 evict", "none")


def test_a_finished_segment_is_compacted_by_the_models_own_summary(owner):
    o = owner(FakePredictor(done=0.9, segment_done=0.9, end=3))
    messages, usage = conversation(o, prompt=35_000, calls=10)
    complete = summary_call()
    asyncio.run(o.before_call(messages, usage, complete))
    assert complete.sent and complete.sent[0][-1]["role"] == "user"            # the summary request
    assert "Found the bug" in messages[1]["content"] and messages[1]["content"].startswith("Fix the bug.")
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "tool"]   # turns 2-3 removed
    assert rules(o)[-1] == ("7 compact", "compacted") and o.s.get("compactions") == 1
    assert o.s.item(1)["in_context"] == 0


@pytest.mark.parametrize("prompt,calls,segment_done", [(20_000, 10, 0.9), (35_000, 10, 0.5), (35_000, 10, 0.69)])
def test_no_compaction_below_the_base_rules(owner, prompt, calls, segment_done):
    o = owner(FakePredictor(done=0.1, segment_done=segment_done, end=3))
    messages, usage = conversation(o, prompt=prompt, calls=calls)
    complete = summary_call()
    asyncio.run(o.before_call(messages, usage, complete))
    assert not complete.sent and len(messages) == 6


def test_compactions_wait_for_the_cooldown(owner):
    o = owner(FakePredictor(done=0.1, segment_done=0.9, end=3))
    messages, usage = conversation(o, prompt=35_000, calls=10)
    asyncio.run(o.before_call(messages, usage, summary_call()))
    later = usage + [{"usage": {"prompt_tokens": 36_000}}] * 4      # 4 calls later: inside the cooldown
    complete = summary_call()
    asyncio.run(o.before_call(messages, later, complete))
    assert not complete.sent and o.s.get("compactions") == 1


def test_small_conversations_never_ask_the_predictor(owner):
    predictor = FakePredictor()
    o = owner(predictor)
    o.after_tool("bash", {"command": "ls"}, "a\nb", False, "c1")
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "t"}]
    asyncio.run(o.before_call(messages, [{"usage": {"prompt_tokens": 3000}}] * 5, summary_call()))
    assert predictor.asked == []


def test_report_counts_rules_and_jev(owner):
    o = owner(FakePredictor(needed=0.05))
    o.after_tool("bash", {"command": "pytest -q"}, BIG, False, "c1")
    report = o.report()
    assert report["decisions"] == {"4 arrival: preview": 1} and report["jev"] == {"calls": 1, "input_tokens": 7}


def test_jev_requests_are_cut_to_fit_its_input_limit():
    from econocontext.predictor.jev import Jev
    from econocontext.tokens import count_tokens
    jev = Jev(max_input_tokens=5_000)
    state = {"task": "t", "conversation": [{"turn": i, "text": "y" * 2000} for i in range(40)],
             "new_tool_result": {"tool": "bash", "lines": "\n".join(f"{i}\t" + "z" * 80 for i in range(1, 2001))}}
    fitted = jev.fit(state)
    assert count_tokens(json.dumps(fitted)) <= 5_000 * 1.05
    assert fitted["conversation"][-1]["turn"] == 39 and len(fitted["conversation"]) >= 2   # recent turns kept
    assert "lines cut to fit" in fitted["new_tool_result"]["lines"]
    small = {"task": "t", "conversation": [{"turn": 1, "text": "hi"}]}
    assert jev.fit(small) == {"task": "t", "conversation": [{"turn": 1, "text": "hi"}]}


# v2.1: no cache in decisions -----------------------------------------------------------
def test_without_cache_planning_a_kept_token_costs_the_full_price_and_edits_break_nothing():
    from econocontext.pricing import lifecycle
    p = lifecycle.from_card("vertex_gemini", "gemini-3.6-flash", 0.81, 1500, cache=False)
    assert (p.c, p.cache_read, p.cache_write, p.hit_share) == (1.0, 1.0, 1.0, 0.0)
    assert p.output == 5.0                                   # output still priced from the card


def test_a_finished_item_far_from_the_end_is_kept_with_cache_planning_and_evicted_without(owner):
    def run(cache):
        o = owner(FakePredictor(done=0.9, segment_done=0.1), cache=cache)
        messages, usage = conversation(o, prompt=12_000, calls=20)
        messages += [{"role": "assistant", "content": "z" * 120_000}]      # ~30k tokens after the item
        asyncio.run(o.before_call(messages, usage, summary_call()))
        return o, messages
    kept, messages = run(cache=True)
    assert rules(kept)[-1] == ("6 evict", "keep") and messages[3]["content"] == BIG   # cache break looked costly
    evicted, messages = run(cache=False)
    assert rules(evicted)[-1] == ("6 evict", "evict") and messages[3]["content"].startswith(NOTE)
