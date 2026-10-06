"""EconoContext v2: each of the nine rules fires when it should and not when it must not.

Hook inputs use the tool response shapes recorded live in spike 1a/B (Claude Code 2.1.286):
Bash {stdout, stderr, ...}, Read {type, file: {content, startLine, ...}}, Agent {content: [...]}.
The predictor is faked; nothing here calls the network.
"""

import json

import pytest
import yaml

from econocontext import decide
from econocontext.pricing import lifecycle
from econocontext.service import ROOT, Service
from econocontext.session import Session, read_only_bash

CFG = yaml.safe_load((ROOT / "config" / "v2.yaml").read_text())
PRICES = lifecycle.from_card("anthropic", "claude-sonnet-5")
BIG = "\n".join(f"line {i}: " + "x" * 60 for i in range(1, 301))  # ~5,000 tokens


class FakePredictor:
    name = "fake"

    def __init__(self, needed=0.1, relevant=None, done=0.9, portion=0.9, subtask=0.9):
        self.needed, self.relevant, self.done, self.portion, self.sub = needed, relevant, done, portion, subtask
        self.calls = []

    def arrival(self, task, convo, item):
        self.calls.append("arrival")
        return {"needed_again": self.needed, "lifetime": "few_calls", "relevant": self.relevant}, {"input_tokens": 10}

    def live(self, task, convo, items):
        self.calls.append("live")
        return {"done": {i["id"]: self.done for i in items}, "phase": "editing",
                "portion_done": self.portion}, {"input_tokens": 20}

    def subtask(self, task, convo, prompt, items):
        self.calls.append("subtask")
        return {"needed": {i["id"]: self.sub for i in items}}, {"input_tokens": 5}


@pytest.fixture
def make(tmp_path):
    def build(predictor=None, force=(), calls=5, prompt=40_000):
        session = Session(tmp_path / "s.sqlite3")
        return decide.Ctx(session, CFG, PRICES, predictor or FakePredictor(), set(force), calls, prompt,
                          "fix the bug", [])
    return build


def bash(command, stdout, agent=None, use_id=None):
    ev = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": command},
          "tool_response": {"stdout": stdout, "stderr": "", "interrupted": False, "isImage": False,
                            "noOutputExpected": False}}
    return {**ev, **({"agent_id": agent} if agent else {}), **({"tool_use_id": use_id} if use_id else {})}


def read(path, start, content, kind="text"):
    file = {"filePath": path, "content": content, "numLines": content.count("\n") + 1,
            "startLine": start, "totalLines": 300} if kind == "text" else {"filePath": path}
    return {"hook_event_name": "PostToolUse", "tool_name": "Read",
            "tool_input": {"file_path": path, "offset": start, "limit": content.count("\n") + 1},
            "tool_response": {"type": kind, "file": file}}


def spec(reply):
    return reply.get("hookSpecificOutput", {})


def rules(ctx):
    return [(d["rule"], d["action"]) for d in ctx.s.decisions()]


# Pricing ---------------------------------------------------------------------------------
def test_prices_come_from_the_card():
    assert (PRICES.cache_read, PRICES.cache_write, PRICES.output) == (0.1, 1.25, 5.0)
    assert PRICES.c == pytest.approx(0.95 * 0.1 + 0.05)          # 0.145 per resident token per call


def test_worked_arrival_example():
    # 5,000 tokens with 20 calls left: full = 5000·1.25 + 5000·0.145·20 = 20,750
    prices = lifecycle.arrival(PRICES, 5000, {"preview": 100}, {"preview": 0.1}, 20, 40_000, reread_at=10)
    assert prices["full"] == pytest.approx(20_750)
    reread = 40_000 * 0.145 + 500 * 5 + 5000 * 1.25 + 5000 * 0.145 * 10   # re-read at call 10, carried 10
    assert prices["preview"] == pytest.approx(100 * 1.25 + 100 * 0.145 * 20 + 0.1 * reread)


def test_content_needed_soon_is_cheaper_to_keep():
    # 5,000 tokens, 25 calls left, 95% needed again within a call: keeping it beats a preview.
    soon = lifecycle.arrival(PRICES, 5000, {"preview": 150}, {"preview": 0.95}, 25, 40_000,
                             lifecycle.reread_at("few_calls", 25))
    assert soon["full"] < soon["preview"]
    late = lifecycle.arrival(PRICES, 5000, {"preview": 150}, {"preview": 0.05}, 25, 40_000,
                             lifecycle.reread_at("whole_task", 25))
    assert late["preview"] < late["full"]


def test_compaction_break_even():
    prices = lifecycle.compaction(PRICES, 100_000, 36_400, 1_500, 20, [])
    assert prices["keep"] == pytest.approx(63_600 * 0.145 * 20)
    fixed = 100_000 * 0.145 + 1_500 * 5 + 1_500 * 1.25
    assert prices["h_star"] == pytest.approx(fixed / ((63_600 - 1_500) * 0.145))
    assert prices["compact"] < prices["keep"]


# Rule 9 and the read-only test ------------------------------------------------------------
def test_writes_bump_the_epoch_and_reads_do_not(make):
    ctx = make()
    decide.post_tool(ctx, bash("cat a.py", "x"))
    assert ctx.s.epoch == 0
    decide.post_tool(ctx, {"hook_event_name": "PostToolUse", "tool_name": "Edit",
                           "tool_input": {"file_path": "a.py"}, "tool_response": {"filePath": "a.py"}})
    decide.post_tool(ctx, bash("python fix.py > out.txt", ""))
    assert ctx.s.epoch == 2 and ("9 invalidate", "bump") in rules(ctx)
    assert read_only_bash("grep -n foo a.py | head -5") and not read_only_bash("sed -i s/a/b/ a.py")


# Rule 1 ------------------------------------------------------------------------------------
def test_identical_output_of_an_identical_call_becomes_a_note(make):
    ctx = make()
    out = "\n".join(f"row {i}" for i in range(200))
    assert decide.post_tool(ctx, bash("ls -la", out)) == {}
    reply = decide.post_tool(ctx, bash("ls -la", out))
    assert "Same output" in spec(reply)["updatedToolOutput"]["stdout"]
    assert set(spec(reply)["updatedToolOutput"]) == {"stdout", "stderr", "interrupted", "isImage", "noOutputExpected"}
    assert ("1 dont_repeat", "note") in rules(ctx)


def test_changed_output_or_a_compacted_copy_is_shown_in_full(make):
    ctx = make()
    out = "\n".join(f"row {i}" for i in range(200))
    decide.post_tool(ctx, bash("ls -la", out))
    assert decide.post_tool(ctx, bash("ls -la", out + "\nnew")) == {}         # output changed
    ctx.s.drop_from_context("main", set())                                   # a compaction dropped both
    assert decide.post_tool(ctx, bash("ls -la", out)) == {}                  # the re-read is shown in full


# Rule 2 ------------------------------------------------------------------------------------
def test_an_overlapping_read_shows_only_new_lines(make):
    ctx = make()
    lines = [f"def f{i}(): return {i}" for i in range(1, 121)]
    decide.post_tool(ctx, read("a.py", 1, "\n".join(lines[:50])))
    reply = decide.post_tool(ctx, read("a.py", 30, "\n".join(lines[29:80])))
    file = spec(reply)["updatedToolOutput"]["file"]
    assert file["startLine"] == 51 and file["content"].split("\n")[0] == "def f51(): return 51"
    assert "Lines 30-50" in spec(reply)["additionalContext"]


def test_an_overlapping_read_of_changed_lines_is_not_trimmed(make):
    ctx = make()
    lines = [f"def f{i}(): return {i}" for i in range(1, 121)]
    decide.post_tool(ctx, read("a.py", 1, "\n".join(lines[:50])))
    changed = [line.replace("return", "yield") for line in lines[29:80]]
    assert decide.post_tool(ctx, read("a.py", 30, "\n".join(changed))) == {}


# Rule 3 ------------------------------------------------------------------------------------
def test_an_unchanged_repeat_gets_a_note_while_the_copy_is_in_context(make):
    ctx = make()
    decide.post_tool(ctx, bash("cat notes.txt", "\n".join(f"hello {i}" for i in range(200))))
    pre = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "cat notes.txt"},
           "tool_use_id": "t2"}
    reply = decide.pre_tool(ctx, pre)
    command = spec(reply)["updatedInput"]["command"]
    assert "Same output as your earlier identical call" in command and "hello 5" not in command
    assert rules(ctx)[-1] == ("1 dont_repeat", "note")
    decide.post_tool(ctx, bash(command, "Same output ...", use_id="t2"))
    assert ctx.s.last_same("main", "Bash", {"command": "cat notes.txt"})["form"] == "note"


def test_an_unchanged_repeat_is_served_in_full_once_the_copy_left_context(make):
    ctx = make()
    decide.post_tool(ctx, bash("cat notes.txt", "hello"))
    ctx.s.drop_from_context("main", set())                             # e.g. after a compaction
    pre = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "cat notes.txt"},
           "tool_use_id": "t2"}
    reply = decide.pre_tool(ctx, pre)
    assert spec(reply)["updatedInput"]["command"].startswith("cat <<'ECONO_EOF'\nhello")
    assert rules(ctx)[-1] == ("3 serve_stored", "serve")
    post = decide.post_tool(ctx, bash(spec(reply)["updatedInput"]["command"], "hello", use_id="t2"))
    assert "saved output" in spec(post)["additionalContext"]
    assert ctx.s.full_copy_in_context("main", "Bash", {"command": "cat notes.txt"})["output"] == "hello"


def test_nothing_is_served_after_a_write_or_for_a_command_that_writes(make):
    ctx = make()
    decide.post_tool(ctx, bash("cat notes.txt", "hello"))
    decide.post_tool(ctx, bash("echo hi > notes.txt", ""))
    pre = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "cat notes.txt"}}
    assert decide.pre_tool(ctx, pre) == {}
    decide.post_tool(ctx, bash("rm -rf build", ""))
    assert decide.pre_tool(ctx, {**pre, "tool_input": {"command": "rm -rf build"}}) == {}


def test_a_repeated_read_of_a_previewed_file_gets_the_full_copy_back(make):
    ctx = make(predictor=FakePredictor(needed=0.01))
    decide.post_tool(ctx, read("big.py", 1, BIG))                            # previewed (rule 4)
    reply = decide.post_tool(ctx, read("big.py", 1, "", kind="file_unchanged"))
    assert spec(reply)["updatedToolOutput"]["file"]["content"] == BIG
    assert ("3 serve_stored", "restore") in rules(ctx)


# Rules 4 and 5 -----------------------------------------------------------------------------
def test_a_big_result_unlikely_to_be_needed_becomes_a_preview(make):
    ctx = make(predictor=FakePredictor(needed=0.05))
    reply = decide.post_tool(ctx, bash("pytest -q", BIG))
    out = spec(reply)["updatedToolOutput"]["stdout"]
    assert out.startswith("line 1:") and "lines not shown" in out and "repeat the identical call" in spec(reply)["additionalContext"]
    rule, action = rules(ctx)[-1]
    assert (rule, action) == ("4 arrival", "preview")


def test_a_big_result_likely_needed_stays_full_unless_forced(make):
    assert decide.post_tool(make(predictor=FakePredictor(needed=0.95)), bash("pytest -q", BIG)) == {}
    forced = make(predictor=FakePredictor(needed=0.95), force=["4 arrival"])
    assert "updatedToolOutput" in spec(decide.post_tool(forced, bash("pytest -q", BIG)))


def test_a_slice_keeps_the_relevant_lines(make):
    ctx = make(predictor=FakePredictor(needed=0.6, relevant={2: 0.9, 0: 0.1}))
    reply = decide.post_tool(ctx, bash("cat log.txt", BIG))
    out = spec(reply)["updatedToolOutput"]["stdout"]
    assert out.startswith("[lines 81-120]") and "line 81:" in out and "line 1:" not in out
    assert rules(ctx)[-1] == ("4 arrival", "slice")


def test_a_big_worker_report_is_priced_as_rule_5(make):
    ctx = make(predictor=FakePredictor(needed=0.05))
    ev = {"hook_event_name": "PostToolUse", "tool_name": "Agent", "tool_input": {"prompt": "explore"},
          "tool_response": {"status": "completed", "agentId": "w1", "content": [{"type": "text", "text": BIG}]}}
    reply = decide.post_tool(ctx, ev)
    assert spec(reply)["updatedToolOutput"]["content"][0]["text"].startswith("line 1:")
    assert rules(ctx)[-1] == ("5 worker_report", "preview")


def test_small_results_never_ask_the_predictor(make):
    predictor = FakePredictor()
    decide.post_tool(make(predictor=predictor), bash("ls", "a\nb"))
    assert predictor.calls == []


# Rules 6 and 7 -----------------------------------------------------------------------------
def test_a_finished_portion_flags_a_compaction_and_post_compact_drops_items(make):
    ctx = make(predictor=FakePredictor(needed=0.95, done=0.9, portion=0.9), calls=10, prompt=120_000)
    decide.post_tool(ctx, bash("cat log.txt", BIG))
    flag = decide.live_check(ctx)
    assert flag and flag["rule"] == "7 compact" and "Summarize" in flag["instructions"]
    decide.post_compact(ctx.s, {"trigger": "manual", "compact_summary": "found the bug"})
    assert ctx.s.in_context("main") == [] and ctx.s.get("compact") is None
    assert ("7 compact", "compacted") in rules(ctx)


def test_no_compaction_for_a_small_conversation_or_unfinished_work(make):
    small = make(calls=10, prompt=40_000)
    assert decide.live_check(small) is None
    busy = make(predictor=FakePredictor(needed=0.95, done=0.1, portion=0.1), calls=10, prompt=120_000)
    decide.post_tool(busy, bash("cat log.txt", BIG))
    assert decide.live_check(busy) is None and rules(busy)[-1] == ("6 evict", "wait")


# Rule 8 ------------------------------------------------------------------------------------
def test_placement_resumes_an_idle_worker_when_forced_and_briefs_otherwise(make):
    ctx = make(force=["8 placement"])
    decide.post_tool(ctx, bash("grep -rn foo src", "src/a.py:3: foo()", agent="w1"))
    ctx.s.set("idle_workers", {"w1": "Explore"})
    pre = {"hook_event_name": "PreToolUse", "tool_name": "Agent", "tool_input": {"prompt": "fix foo"}}
    assert "SendMessage" in spec(decide.pre_tool(ctx, pre))["permissionDecisionReason"]

    ctx2 = make(predictor=FakePredictor(subtask=0.9))
    decide.post_tool(ctx2, bash("grep -rn foo src", "src/a.py:3: foo()", agent="w1"))
    ctx2.s.set("idle_workers", {"w1": "Explore"})
    reply = decide.pre_tool(ctx2, pre)
    action = rules(ctx2)[-1][1]
    assert action in ("resume", "brief")
    if action == "brief":
        assert "src/a.py:3" in spec(reply)["updatedInput"]["prompt"]


def test_no_placement_decision_without_an_idle_worker(make):
    ctx = make()
    pre = {"hook_event_name": "PreToolUse", "tool_name": "Agent", "tool_input": {"prompt": "fix foo"}}
    assert decide.pre_tool(ctx, pre) == {} and rules(ctx) == []


# The service -------------------------------------------------------------------------------
def test_the_service_routes_hooks_by_run_and_session(tmp_path):
    service = Service(CFG)
    service.register({"run": "r1", "predictor": "prior", "sessions_dir": str(tmp_path / "r1")})
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps({"type": "assistant", "message": {"id": "m1", "role": "assistant",
                          "content": [], "usage": {"input_tokens": 2, "cache_read_input_tokens": 36_400}}}) + "\n")
    base = {"session_id": "s1", "transcript_path": str(transcript)}
    service.hook("r1", {**base, "hook_event_name": "UserPromptSubmit", "prompt": "fix it"})
    out = "\n".join(f"row {i}" for i in range(200))
    service.hook("r1", {**base, **bash("ls -la", out)})
    reply = service.hook("r1", {**base, **bash("ls -la", out)})
    assert "Same output" in spec(reply)["updatedToolOutput"]["stdout"]
    assert service.hook("unknown", {**base, **bash("ls", "a")}) == {}
    assert (tmp_path / "r1" / "s1.sqlite3").exists() and service.flag("r1") == {"compact": None}


def test_a_forced_compaction_waits_for_work_and_happens_once(make):
    early = make(force=["7 compact"], calls=3, prompt=40_000)
    assert decide.live_check(early) is None                       # too early in the session
    ctx = make(force=["7 compact"], calls=12, prompt=40_000)
    assert decide.live_check(ctx)["rule"] in ("6 evict", "7 compact")
    decide.post_compact(ctx.s, {"trigger": "manual", "compact_summary": "s"})
    ctx.calls = 30
    assert decide.live_check(ctx) is None                         # once only


def test_a_tiny_repeat_is_served_because_a_note_would_cost_more(make):
    ctx = make()
    decide.post_tool(ctx, bash("cat notes.txt", "hello"))
    pre = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "cat notes.txt"}}
    assert spec(decide.pre_tool(ctx, pre))["updatedInput"]["command"].startswith("cat <<'ECONO_EOF'\nhello")
    assert rules(ctx)[-1] == ("3 serve_stored", "serve")


def test_the_flag_is_found_for_a_run_name_with_a_plus(tmp_path):
    """The driver asks for the flag over HTTP; a '+' in the run name must survive the query string."""
    import threading
    import urllib.parse
    import urllib.request
    from http.server import ThreadingHTTPServer
    from econocontext.service import make_handler
    service = Service(CFG)
    run = "v2x:econo+jev:maven"
    service.register({"run": run, "predictor": "prior", "sessions_dir": str(tmp_path)})
    session, _ = service.session(run, "s1")
    session.set("compact", {"instructions": "x", "keep_ids": [], "rule": "7 compact"})
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(service))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/flag?run={urllib.parse.quote(run, safe='')}"
        assert json.load(urllib.request.urlopen(url))["compact"]["instructions"] == "x"
    finally:
        server.shutdown()


def test_measured_compaction_cost_makes_compaction_rare():
    # v2x2 maven at call 13: prompt ~60k, 25 calls total. With the measured summary (10,630 kept,
    # 16,783 output) compaction does not pay; with the old 1,500 placeholder it looked like it did.
    measured = lifecycle.compaction(PRICES, 60_000, 36_400, 10_630, 12, [], summary_output=16_783)
    assert measured["compact"] > measured["keep"] and measured["h_star"] > 40
    placeholder = lifecycle.compaction(PRICES, 60_000, 36_400, 1_500, 12, [])
    assert placeholder["h_star"] < measured["h_star"]
