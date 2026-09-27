"""Context policies: each rule on a known history, without a model."""

import asyncio
from types import SimpleNamespace

import pytest

from econocontext.assembler import validate_protocol
from econocontext.config import Config, Pricing
from econocontext.context import CLEARED, CacheAware, ClearOldest, Compact
from econocontext.contracts import ModelResponse

BIG = "x" * 6000  # ~2,400 estimated tokens


def history(turns, output=BIG):
    messages = [dict(role="user", content="goal")]
    for i in range(turns):
        messages.append(
            dict(
                role="assistant",
                content=None,
                tool_calls=[
                    dict(id=f"c{i}", type="function", function=dict(name="read", arguments="{}"))
                ],
            )
        )
        messages.append(dict(role="tool", tool_call_id=f"c{i}", content=output))
    return messages


class Memory:
    def __init__(self, messages):
        self.messages, self.events = messages, []

    async def history(self, worker_id):
        return [dict(m) for m in self.messages]

    async def event(self, run_id, kind, data):
        self.events.append((kind, data))


def fake(messages, backend=None, telemetry=None):
    return SimpleNamespace(memory=Memory(messages), backend=backend, telemetry=telemetry)


WORKER = SimpleNamespace(id="root", run_id="run", role="root")


def assembled(tokens):
    return SimpleNamespace(tokens=tokens)


def config(**kw):
    kw.setdefault(
        "pricing", Pricing(input_per_million=1, cached_per_million=0.1, output_per_million=1)
    )
    return Config(**kw)


def test_clear_oldest_keeps_the_newest_three_over_the_threshold():
    messages = history(6)
    policy, manager = ClearOldest(config(context_trigger=1000)), fake(messages)
    assert not asyncio.run(policy.before_call(manager, WORKER, {}, assembled(999)))
    assert asyncio.run(policy.before_call(manager, WORKER, {}, assembled(1001)))
    rendered = policy.render("root", messages)
    tools = [m["content"] for m in rendered if m["role"] == "tool"]
    assert tools == [CLEARED] * 3 + [BIG] * 3
    validate_protocol(rendered)


def run_calls(policy, manager, n):
    return [asyncio.run(policy.before_call(manager, WORKER, {}, assembled(0))) for _ in range(n)]


def test_cache_aware_clears_at_once_when_nothing_is_cached():
    # Input and cached input cost the same: clearing has no cascade to pay.
    flat = Pricing(input_per_million=1, cached_per_million=1, output_per_million=1)
    policy, manager = CacheAware(config(pricing=flat)), fake(history(6))
    assert run_calls(policy, manager, 1) == [True]
    event = manager.memory.events[-1][1]
    assert event["break_at"] == 2 and len(event["indexes"]) == 3


def test_cache_aware_waits_until_rent_pays_for_the_rewrite():
    # Anthropic-like: a write costs 1.25x, a read 0.1x. The oldest output has five
    # outputs after it, so clearing it rewrites them all; it waits.
    anthropic = Pricing(
        input_per_million=1,
        cached_per_million=0.1,
        cache_write_per_million=1.25,
        output_per_million=1,
    )
    policy, manager = CacheAware(config(pricing=anthropic)), fake(history(6))
    decisions = run_calls(policy, manager, 60)
    first = decisions.index(True)
    assert first > 10
    # Batched: the earliest qualifying break takes every eligible output after it.
    event = manager.memory.events[0][1]
    assert event["indexes"] == [i for i in (2, 4, 6) if i >= event["break_at"]]


def test_cache_aware_sweeps_when_the_cache_has_gone_cold():
    anthropic = Pricing(
        input_per_million=1,
        cached_per_million=0.1,
        cache_write_per_million=1.25,
        output_per_million=1,
    )
    policy, manager = CacheAware(config(pricing=anthropic, cache_ttl=0.01)), fake(history(6))
    assert run_calls(policy, manager, 1) == [False]
    import time

    time.sleep(0.02)
    assert run_calls(policy, manager, 1) == [True]
    assert manager.memory.events[-1][1]["cold_cache"]


def test_compact_summarizes_the_middle_with_a_billed_call():
    messages = history(12, output="short")
    calls = []

    class Backend:
        async def complete(self, request, context):
            calls.append(request)
            return ModelResponse(
                message=dict(role="assistant", content="SUMMARY"),
                usage=dict(prompt_tokens=100, completion_tokens=10),
            )

    class Telemetry:
        async def begin(self, run_id, **kw):
            assert kw["phase"] == "compaction"
            return {}

        async def finish(self, attempt, **kw):
            return dict(cost=0.5)

    state = dict(known_cost=0)
    policy = Compact(
        config(context_trigger=10, model="m", backend="live", live_context_tokens=1000)
    )
    manager = fake(messages, Backend(), Telemetry())
    assert asyncio.run(policy.before_call(manager, WORKER, state, assembled(11)))
    rendered = policy.render("root", messages)
    assert rendered[0]["content"] == "goal" and "SUMMARY" in rendered[1]["content"]
    assert rendered[2]["role"] == "assistant" and len(rendered) < len(messages)
    validate_protocol(rendered)
    assert state["known_cost"] == 0.5 and len(calls) == 1


def test_holding_cost_favours_leaving_less_in_a_long_lived_root():
    from econocontext.contracts import CandidatePlan, Mode
    from econocontext.planning.cost_model import CostModel

    pricing = Pricing(input_per_million=1, cached_per_million=0.1, output_per_million=1)
    model = CostModel(config(pricing=pricing, lifetime_prior=60))
    root = SimpleNamespace(id="root")
    observation = dict(inline=dict(role="tool", tool_call_id="c", content=BIG))
    inline = CandidatePlan(
        operation_id="o", state_revision="r0", mode=Mode.CONTINUE, worker_id="root"
    )
    child = CandidatePlan(operation_id="o", state_revision="r0", mode=Mode.FRESH, view="FOCUSED")

    def cost(plan, calls_made):
        state = dict(observation=observation, worker=root, root_calls=calls_made)
        return model.holding(state, plan)

    # Early, inline content is carried for ~60 more calls; late, for one.
    assert cost(inline, 0) == pytest.approx(2 * cost(child, 0))
    assert cost(inline, 0) > 5 * cost(inline, 59)
    assert (
        CostModel(config(pricing=pricing)).holding(
            dict(observation=observation, worker=root), inline
        )
        is None
    )
