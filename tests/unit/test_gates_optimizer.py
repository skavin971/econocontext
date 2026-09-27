from econocontext.optimizer.gates import check
from econocontext.optimizer.optimizer import select
from econocontext.types import Candidate, Constraints, Intercept, Objective, OperatorType
from tests.unit.conftest import conversation


def context(engine, intercept=Intercept.ADMIT_TOOL_RESULT, **allow):
    eco = engine()
    ctx = eco._context("run-1:root", intercept, conversation())
    ctx.allowlist.update(allow)
    return eco, ctx


def cand(name, default=False, risk=0.0, **payload):
    return Candidate(name, OperatorType.EXACT if risk == 0 else OperatorType.APPROXIMATE, risk,
                     default, payload)


def test_pointer_rejected_when_exact_bytes_are_needed(engine):
    _, ctx = context(engine, POINTER=True)
    ctx.constraints.max_quality_risk = 1.0
    ok, why = check(cand("POINTER", risk=0.2, needs_exact_bytes=True), ctx)
    assert not ok and why.startswith("fidelity")


def test_reuse_rejected_after_a_source_version_changes(engine):
    eco, ctx = context(engine, REUSE_RESULT=True)
    eco.db.bump("run-1", ["/testbed/src/app.py"])
    ctx.current_versions = eco.db.current_versions("run-1")
    ok, why = check(cand("REUSE_RESULT", from_store=True, read_set={"/testbed/src/app.py": "0"}), ctx)
    assert not ok and why.startswith("version")


def test_side_effecting_results_are_never_reused(engine):
    _, ctx = context(engine, ANSWER_FROM_STORE=True)
    ok, why = check(cand("ANSWER_FROM_STORE", from_store=True, side_effect=True, read_set={}), ctx)
    assert not ok and why.startswith("side_effects")


def test_respects_quality_and_latency_limits_and_records_why_not(engine, cfg):
    _, ctx = context(engine, POINTER=True)
    constraints = Constraints(objective=Objective.COST, max_quality_risk=0.0)
    keep = cand("KEEP_FULL", default=True, result_tokens=5000, resident_tokens=5000)
    pointer = cand("POINTER", risk=0.2, result_tokens=100, resident_tokens=100,
                   reread_tokens=5000, p_need_again=0.1)
    d = select([keep, pointer], ctx, constraints, cfg.raw)
    assert d.chosen.name == "KEEP_FULL" and "quality" in d.rejected[0].why_not
    ctx.constraints = constraints = Constraints(max_quality_risk=1.0, max_latency_ms=0.0)
    slow = cand("FRESH", default=True, extra_calls=5, extra_call_input_tokens=100)
    d = select([slow, cand("REUSE_RESULT", from_store=True, read_set={})], ctx, constraints, cfg.raw)
    assert any("max_latency_ms" in r.why_not for r in d.rejected)


def test_ties_go_to_lower_latency_then_the_host_default(engine, cfg):
    _, ctx = context(engine, Intercept.BEFORE_TOOL_CALL, ANSWER_FROM_STORE=True)
    run = cand("RUN_TOOL", default=True, runs_tool=True, result_tokens=50, resident_tokens=50)
    store = cand("ANSWER_FROM_STORE", from_store=True, result_tokens=50, resident_tokens=50,
                 read_set={}, side_effect=False)
    d = select([run, store], ctx, Constraints(), cfg.raw)
    assert d.chosen.name == "ANSWER_FROM_STORE"      # equal NU, 1 ms beats 32 ms
    _, ctx = context(engine, Intercept.PLAN_PROMPT, ZONED=True)
    as_is = cand("AS_IS", default=True, send_tokens=100, model_calls=1, resident_tokens=100)
    zoned = cand("ZONED", send_tokens=100, model_calls=1, resident_tokens=100)
    d = select([zoned, as_is], ctx, Constraints(), cfg.raw)
    assert d.chosen.name == "AS_IS" and d.feasible == ["ZONED", "AS_IS"]  # full tie: host default
