"""Context policies: what a long-lived worker's history looks like on the next call.

The stored history is append-only and never edited. A policy records decisions
against it by message index -- "this tool output is cleared", "everything before
index 40 is replaced by this summary" -- and the assembler renders history
through those decisions. The transcript stays complete for auditing, and every
policy is judged on the same stored run.

Four policies, the live study's only variable:

  append-only   never evicts. The naive baseline.
  compact       over a token threshold, summarize the middle of the history with
                a billed model call; keep the goal and the recent tail.
                (OpenHands condenser, Claude Code auto-compact.)
  clear-oldest  over a token threshold, clear every tool output but the newest
                few. (LangChain ClearToolUsesEdit.)
  cache-aware   clear an old tool output only when the rent already paid to keep
                it reaches what clearing it now would cost in cache rewrites;
                batch every clearing to the earliest break point; sweep when the
                cache has gone cold. Same newest-few floor as clear-oldest, so the
                two differ only in when and what they clear.

Policies see prices only through the Pricing record. Nothing here knows which
provider is behind them.
"""

import asyncio
import time

from .assembler import token_count
from .contracts import canonical

CLEARED = "[cleared]"


class AppendOnly:
    name = "append-only"

    def __init__(self, config):
        self.config = config
        self.cleared = {}  # worker id -> set of history indexes
        self.summary = {}  # worker id -> (first kept index, summary text)

    def render(self, worker_id, history):
        """History as the next request will carry it. Pure, given recorded decisions."""
        cleared = self.cleared.get(worker_id, set())
        rendered = []
        for index, message in enumerate(history):
            if index in cleared:
                message = dict(message, content=CLEARED)
                message.pop("_evidence", None)
            rendered.append(message)
        if worker_id in self.summary:
            start, text = self.summary[worker_id]
            note = dict(role="user", content=f"Summary of the earlier work on this task:\n{text}")
            rendered = rendered[:1] + [note] + rendered[start:]
        return rendered

    async def before_call(self, manager, worker, state, assembled):
        """Record new decisions for this worker. True if the rendering changed."""
        return False

    def event(self, manager, worker, kind, **data):
        return manager.memory.event(worker.run_id, kind, dict(data, policy=self.name))


def tool_indexes(history):
    return [i for i, m in enumerate(history) if m.get("role") == "tool"]


class ClearOldest(AppendOnly):
    name = "clear-oldest"

    async def before_call(self, manager, worker, state, assembled):
        if assembled.tokens <= self.config.context_trigger:
            return False
        history = await manager.memory.history(worker.id)
        tools = tool_indexes(history)
        keep = set(tools[-self.config.context_keep :]) if self.config.context_keep else set()
        cleared = self.cleared.setdefault(worker.id, set())
        new = [i for i in tools if i not in keep and i not in cleared]
        if not new:
            return False
        cleared.update(new)
        await self.event(
            manager, worker, "context_cleared", indexes=new, tokens_before=assembled.tokens
        )
        return True


SUMMARY_PROMPT = (
    "You are compacting the working history of a software engineering agent so it "
    "can keep going with less context. Write a summary the agent can continue from: "
    "the goal, what has been established (files, functions, line numbers, causes), "
    "what has been changed and whether it was tested, and what remains. Be specific "
    "and complete; drop only what is no longer needed."
)


class Compact(AppendOnly):
    name = "compact"
    keep_last = 10

    async def before_call(self, manager, worker, state, assembled):
        if assembled.tokens <= self.config.context_trigger:
            return False
        history = await manager.memory.history(worker.id)
        start = self.summary.get(worker.id, (1, None))[0]
        # The kept tail must open on an assistant turn, so no tool result is kept
        # without the call it answers.
        cut = next(
            (
                i
                for i in range(max(start, len(history) - self.keep_last), len(history))
                if history[i].get("role") == "assistant"
            ),
            None,
        )
        if cut is None or cut <= start:
            return False
        previous = self.summary.get(worker.id, (None, None))[1]
        text = await self.summarize(manager, worker, state, previous, history[start:cut])
        self.summary[worker.id] = (cut, text)
        await self.event(
            manager,
            worker,
            "context_compacted",
            summarized=[start, cut],
            tokens_before=assembled.tokens,
            summary_chars=len(text),
        )
        return True

    async def summarize(self, manager, worker, state, previous, messages):
        """One billed model call, recorded like any other so its cost is counted."""
        from .runtime.telemetry import render_messages

        transcript = "\n".join(render_messages(messages))
        body = (f"Earlier summary:\n{previous}\n\n" if previous else "") + (
            f"History to summarize:\n{transcript}"
        )
        request = dict(
            model=self.config.model,
            messages=[
                dict(role="system", content=SUMMARY_PROMPT),
                dict(role="user", content=body),
            ],
            **{self.config.output_parameter: 4096},
        )
        from .runtime.agent_loop import backoff, retryable

        retry_of = None
        retries = state["limits"].retries if "limits" in state else 0
        for retry in range(retries + 1):
            attempt = await manager.telemetry.begin(
                worker.run_id,
                worker_id=worker.id,
                kind="model",
                name=self.config.model,
                request={"body": request},
                phase="compaction",
                retry_of=retry_of,
            )
            started = time.monotonic()
            try:
                response = await manager.backend.complete(request, dict(worker_id=worker.id))
            except Exception as exc:
                await manager.telemetry.finish(
                    attempt,
                    duration=time.monotonic() - started,
                    status="failed",
                    error=type(exc).__name__,
                )
                retry_of = attempt.get("id")
                if not retryable(exc) or retry == retries:
                    raise
                await asyncio.sleep(backoff(exc, retry))
                continue
            completed = await manager.telemetry.finish(
                attempt,
                duration=time.monotonic() - started,
                status="succeeded",
                response=response.message,
                raw_usage=response.usage,
            )
            state["known_cost"] += completed["cost"] or 0
            break
        return response.message.get("content") or ""


class CacheAware(AppendOnly):
    """Clear old tool outputs on a ski-rental rule, priced by the provider's cache.

    For a tool output of l tokens with t tokens after it, clearing it now costs
    (w - r)*t - r*l once -- everything after the break is rewritten -- and saves
    r*l on every later call. Keeping it costs r*l per call. The rule clears it
    once the rent already paid reaches the one-time price. A negative price, as
    with a provider that caches nothing, clears at once; a cold cache makes the
    price negative for everything, since the prefix is rewritten either way.
    Clearing at the earliest qualifying break makes everything eligible after it
    free to clear too, so the whole tail past the break goes in one batch.
    """

    name = "cache-aware"
    min_tokens = 256

    def __init__(self, config):
        super().__init__(config)
        self.calls = {}  # worker id -> calls made
        self.entered = {}  # worker id -> {history index: call number first sent}
        self.last_call = {}  # worker id -> monotonic time of the last call

    def rates(self):
        pricing = self.config.pricing
        write = pricing.cache_write_per_million
        write = pricing.input_per_million if write is None else write
        return pricing.cached_per_million, write

    async def before_call(self, manager, worker, state, assembled):
        now = time.monotonic()
        calls = self.calls[worker.id] = self.calls.get(worker.id, 0) + 1
        last = self.last_call.get(worker.id)
        self.last_call[worker.id] = now
        cold = last is not None and now - last > self.config.cache_ttl
        history = await manager.memory.history(worker.id)
        entered = self.entered.setdefault(worker.id, {})
        for i in range(len(history)):
            entered.setdefault(i, calls)
        if not self.config.pricing:
            return False
        read, write = self.rates()
        cleared = self.cleared.setdefault(worker.id, set())
        rendered = self.render(worker.id, history)
        sizes = [token_count(canonical(m), 1.0) for m in rendered]
        # Rendering may insert a summary; this policy never does, so indexes align.
        tools = tool_indexes(history)
        keep = set(tools[-self.config.context_keep :]) if self.config.context_keep else set()
        eligible = [
            i for i in tools if i not in keep and i not in cleared and sizes[i] >= self.min_tokens
        ]
        if not eligible:
            return False
        total = sum(sizes)
        position, qualifying = 0, None
        offsets = []
        for size in sizes:
            offsets.append(position)
            position += size
        for i in eligible:
            size = sizes[i]
            tail = total - offsets[i] - size
            price = -write * size if cold else (write - read) * tail - read * size
            rent = read * size * (calls - entered[i])
            if rent >= price:
                qualifying = i
                break
        if qualifying is None:
            return False
        batch = [i for i in eligible if i >= qualifying]
        cleared.update(batch)
        await self.event(
            manager,
            worker,
            "context_cleared",
            indexes=batch,
            break_at=qualifying,
            cold_cache=cold,
            tokens_before=assembled.tokens,
            call=calls,
        )
        return True


POLICIES = {p.name: p for p in (AppendOnly, ClearOldest, Compact, CacheAware)}


def build_policy(config):
    return POLICIES[config.context_policy](config)
