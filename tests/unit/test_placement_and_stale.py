"""The planner's two new decisions, on plain data:
- COMMIT_PENDING: point out old tool results that are unlikely to be needed again, when
  the saving beats the expected re-read and the one-time cache break;
- RESUME: send a sub-task to an idle worker that already holds its files."""

from datetime import date

from econocontext import config
from econocontext.assembler.assembler import pointer_text
from econocontext.engine import make_segment
from econocontext.optimizer.optimizer import select
from econocontext.planner import planner
from econocontext.pricing.rates import ratios
from econocontext.tokens import count_tokens
from econocontext.types import AgentNode, Intercept, PlanContext, SegmentKind

CFG = config.load("config", {"allowlist": {"COMMIT_PENDING": True, "RESUME": True},
                             "constraints": {"max_quality_risk": 0.2}})
BIG = "\n".join(f"line {i}: " + "x" * 80 for i in range(150))  # ~3,300 tokens


def ctx(window, h=20, rate=0.3):
    return PlanContext(run_id="r", agent=AgentNode("r:root", None, None),
                       intercept=Intercept.PLAN_PROMPT, window=window, window_max_tokens=10**6,
                       current_versions={}, constraints=CFG.constraints,
                       allowlist=dict(CFG.raw["allowlist"]),
                       rates=ratios(CFG.card, date(2026, 9, 29)), remaining_turns=h,
                       resident_rate=rate)


def conversation():
    seg = lambda n, kind, text, role, **kw: make_segment("r", "r:root", n, kind, text, role=role, **kw)
    return [seg("sys", SegmentKind.SYSTEM, "system prompt", "system"),
            seg("task", SegmentKind.TASK, "fix the bug", "user"),
            seg("c1", SegmentKind.TOOL_CALL, "read a.py", "assistant", pair_id="t1"),
            seg("t1", SegmentKind.TOOL_RESULT, BIG, "tool", pair_id="t1", source="tool:sys_os_read"),
            seg("c2", SegmentKind.TOOL_CALL, "read b.py", "assistant", pair_id="t2"),
            seg("t2", SegmentKind.TOOL_RESULT, "short", "tool", pair_id="t2", source="tool:sys_os_read")]


def pointer_len(s):
    return count_tokens(pointer_text(s, "<path>", 10))


def test_an_old_unused_result_is_pointed_out():
    window = conversation()
    edits = planner.stale_edits(ctx(window), window, {window[3].id: 0}, 12,
                                lambda tool, age: 0.02, pointer_len)
    assert [e["segment_id"] for e in edits] == [window[3].id] and edits[0]["age"] == 12
    decision = select(planner.for_prompt(ctx(window), CFG.raw, [], edits, cache_warm=True),
                      ctx(window), CFG.constraints, CFG.raw)
    assert decision.chosen.name == "COMMIT_PENDING"


def test_a_likely_needed_result_is_kept():
    window = conversation()
    assert planner.stale_edits(ctx(window), window, {}, 12, lambda tool, age: 0.9, pointer_len) == []


def test_the_cache_break_can_outweigh_a_small_saving():
    window = conversation()
    edits = planner.stale_edits(ctx(window, h=1), window, {window[3].id: 0}, 12,
                                lambda tool, age: 0.0, pointer_len)
    decision = select(planner.for_prompt(ctx(window, h=1), CFG.raw, [], edits, cache_warm=True),
                      ctx(window, h=1), CFG.constraints, CFG.raw)
    costs = decision.candidate_costs
    assert costs["COMMIT_PENDING"].prepare > costs["AS_IS"].prepare  # the break is priced


WORKERS = [dict(worker_id="r:worker:aa", title="finder", files=["src/a.py"],
                resident_tokens=9000, calls=4, warm=True, busy=False),
           dict(worker_id="r:worker:bb", title="tester", files=["src/b.py"],
                resident_tokens=9000, calls=4, warm=True, busy=True)]
FILES = {"src/a.py": 3000, "src/b.py": 3000}


def test_resume_goes_to_the_idle_worker_that_holds_the_file():
    c = ctx([], h=10)
    candidates = planner.for_placement(c, CFG.raw, "check how src/a.py parses flags", WORKERS, 5, FILES)
    resume = next(x for x in candidates if x.name == "RESUME")
    assert resume.payload["title"] == "finder" and resume.payload["held_tokens"] == 3000
    assert select(candidates, c, CFG.constraints, CFG.raw).chosen.name == "RESUME"


def test_no_resume_for_a_busy_worker_or_unrelated_task():
    c = ctx([], h=10)
    names = lambda task: [x.name for x in planner.for_placement(c, CFG.raw, task, WORKERS, 5, FILES)]
    assert names("run the tests in src/b.py") == ["FRESH"]  # its holder is busy
    assert names("summarize the README") == ["FRESH"]
