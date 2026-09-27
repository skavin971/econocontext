"""Attempt accounting independent of the planner, also usable by external runners."""

import json
import math
import statistics
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from ..contracts import digest, now, uid


def normalize(raw):
    if raw is None:
        return dict(
            uncached=None, cached=None, cache_write=None, output=None, complete=False, error=None
        )
    try:
        total = raw["prompt_tokens"]
        reported_total = raw.get("total_tokens")
        reasoning = (raw.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
        output = raw.get("completion_tokens")
        # Some providers omit completion_tokens entirely when a turn produces no
        # content tokens; the reported total still accounts for the work done.
        if output is None and reported_total is not None:
            output = reported_total - total - reasoning
        if output is None:
            raise KeyError("completion_tokens")
        # Providers disagree on whether reasoning tokens are already inside
        # completion_tokens. The provider's own total disambiguates, and both
        # categories bill at the output rate either way.
        excluded = reasoning and reported_total == total + output + reasoning
        if excluded:
            output += reasoning
        details = raw.get("prompt_tokens_details") or {}
        cached = details.get("cached_tokens", 0)
        # Backends for explicit-cache providers report what this call wrote into
        # the cache; nobody else writes at a premium, so absent means zero.
        cache_write = details.get("cache_write_tokens", 0)
        cache_reported = "cached_tokens" in details
        if (
            any(
                isinstance(x, bool) or not isinstance(x, int) or x < 0
                for x in (total, cached, cache_write, output)
            )
            or cached + cache_write > total
        ):
            raise ValueError("Invalid disjoint token counts")
        return dict(
            uncached=total - cached - cache_write,
            cached=cached,
            cache_write=cache_write,
            output=output,
            reasoning=reasoning,
            complete=True,
            cache_reported=cache_reported,
            error=None,
        )
    except (KeyError, ValueError, TypeError) as exc:
        return dict(
            uncached=None,
            cached=None,
            cache_write=None,
            output=None,
            complete=False,
            error=str(exc),
        )


def charge(usage, pricing):
    if not pricing or not usage["complete"]:
        return None
    # No reported cache split means nothing was discounted, so the whole prompt
    # prices at the uncached rate. That can only overstate, never understate, and
    # an overstated charge is more useful than an absent one: dropping these calls
    # from the total silently understated real runs by roughly 2.5x. Consumers that
    # need to know which it was read cache_reported on the usage record.
    write_rate = pricing.cache_write_per_million
    if write_rate is None:
        write_rate = pricing.input_per_million
    return (
        usage["uncached"] * pricing.input_per_million
        + usage["cached"] * pricing.cached_per_million
        + (usage.get("cache_write") or 0) * write_rate
        + usage["output"] * pricing.output_per_million
    ) / 1_000_000


RULE = "=" * 100
THIN = "-" * 100


def render_messages(messages):
    out = []
    for message in messages:
        role = message.get("role", "?")
        header = f"  [{role}]"
        if message.get("tool_call_id"):
            header += f" (responding to {message['tool_call_id']})"
        out.append(header)
        if message.get("content"):
            for line in str(message["content"]).splitlines() or [""]:
                out.append("      " + line)
        for call in message.get("tool_calls") or []:
            function = call.get("function", {})
            out.append(
                f"      -> {function.get('name')}({function.get('arguments')})"
                f"   [id {call.get('id')}]"
            )
        out.append("")
    return out


class Telemetry:
    def __init__(self, memory, config):
        self.memory, self.config = memory, config
        self.first_seen = {}
        self.last_end = {}
        self.call_index = defaultdict(int)
        self.run_log = {}

    def log_path(self, run_id):
        """One file per run, opened on the first call and named for when it began.

        A single shared log appends across every run, which has already spoiled
        one measurement here: growth attributed to a run was really the previous
        run still sitting in the file. A per-run file cannot be misread that way.
        """
        root = self.config.call_log
        if not root:
            return None
        path = self.run_log.get(run_id)
        if path is None:
            path = Path(root) / f"run-{int(time.time())}-{run_id}.txt"
            self.run_log[run_id] = path
        return path

    def timing(self, attempt):
        """Per-call latency plus where this call sits in the run's elapsed time."""
        run_id = attempt["run_id"]
        try:
            started = datetime.fromisoformat(attempt["started"])
            ended = datetime.fromisoformat(attempt["ended"])
        except (TypeError, ValueError):
            return "LATENCY  unavailable"
        origin = self.first_seen.setdefault(run_id, started)
        previous = self.last_end.get(run_id)
        self.last_end[run_id] = ended
        self.call_index[run_id] += 1
        latency = attempt.get("duration") or (ended - started).total_seconds()
        parts = [
            f"LATENCY  call #{self.call_index[run_id]}",
            f"request_to_response={latency:.3f}s",
            f"run_elapsed_at_end={(ended - origin).total_seconds():.3f}s",
        ]
        if previous is not None:
            # Time between the last response and this request: local work, not the model.
            parts.append(f"local_gap_before={(started - previous).total_seconds():.3f}s")
        output = (attempt.get("usage") or {}).get("output")
        if attempt["kind"] == "model" and output and latency > 0:
            parts.append(f"throughput={output / latency:.1f} out_tok/s")
        return "  ".join(parts)

    def log_call(self, attempt, response):
        """Append a full human-readable record of one call to this run's log."""
        path = self.log_path(attempt["run_id"])
        if not path:
            return
        raw = attempt.get("raw_usage") or {}
        usage = attempt.get("usage") or {}
        reasoning = (raw.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)
        lines = [
            RULE,
            f"[{attempt['ended']}]  {attempt['kind'].upper()}  {attempt['name']}",
            f"  attempt={attempt['id']}  run={attempt['run_id']}  worker={attempt['worker_id']}",
            f"  phase={attempt['phase']}  status={attempt['status']}",
            f"  operation={attempt['operation_id']}  plan={attempt['plan_id']}",
        ]
        if attempt.get("error"):
            lines.append(f"  ERROR: {attempt['error']}")
        try:
            request = self.memory.artifacts.read_json(attempt["request"])
        except Exception:
            request = None
        lines.append(THIN)
        if attempt["kind"] == "model" and isinstance(request, dict):
            messages = (request.get("body") or request).get("messages") or []
            lines.append(
                f"INPUT  ({len(messages)} messages, prompt_tokens={raw.get('prompt_tokens')})"
            )
            lines.extend(render_messages(messages))
        else:
            lines.append("INPUT")
            lines.append("      " + json.dumps(request))
            lines.append("")
        lines.append(THIN)
        lines.append(
            f"OUTPUT (completion={raw.get('completion_tokens')}"
            f"  reasoning={reasoning}  billed_output={usage.get('output')})"
        )
        if isinstance(response, dict) and (response.get("content") or response.get("tool_calls")):
            lines.extend(render_messages([response]))
        else:
            lines.append("      " + json.dumps(response)[:20000])
            lines.append("")
        lines.append(THIN)
        lines.append(
            f"TOKENS  prompt={raw.get('prompt_tokens')}  completion={raw.get('completion_tokens')}"
            f"  reasoning={reasoning}  total={raw.get('total_tokens')}"
            f"  billed_input={usage.get('uncached')}  cached={usage.get('cached')}"
            f"  cache_write={usage.get('cache_write')}"
            f"  billed_output={usage.get('output')}"
        )
        cost = attempt.get("cost")
        lines.append(
            f"COST    {'$%.6f' % cost if cost is not None else 'unknown'}"
            f"   cache_reported={usage.get('cache_reported', False)}"
        )
        lines.append(self.timing(attempt))
        lines.append(RULE)
        lines.append("")
        lines.append("")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write("\n".join(line for line in lines if line is not None) + "\n")
        except OSError:
            pass

    async def begin(
        self,
        run_id,
        *,
        worker_id,
        kind,
        name,
        request,
        operation_id=None,
        plan_id=None,
        phase="execution",
        retry_of=None,
        attempt_id=None,
    ):
        attempt = dict(
            id=attempt_id or uid(),
            run_id=run_id,
            worker_id=worker_id,
            kind=kind,
            name=name,
            operation_id=operation_id,
            plan_id=plan_id,
            phase=phase,
            request=self.memory.artifacts.json(request),
            started=now(),
            ended=None,
            status="running",
            duration=None,
            retry_of=retry_of,
            usage=normalize(None),
            raw_usage=None,
            cost=None,
            synthetic=self.config.backend == "fake",
            fingerprint=self.config.fingerprint(),
            pricing=self.config.pricing.model_dump() if self.config.pricing else None,
        )
        # Stable ingestion IDs are idempotent, including interrupted external calls.
        try:
            existing = await self.memory.get(attempt["id"], run_id)
            return existing
        except KeyError:
            await self.memory.save("attempt", attempt)
            return attempt

    async def finish(self, attempt, *, duration, status, response=None, raw_usage=None, error=None):
        existing = await self.memory.get(attempt["id"], attempt["run_id"])
        if existing["status"] != "running":
            return existing
        usage = (
            normalize(raw_usage)
            if attempt["kind"] == "model"
            else dict(uncached=0, cached=0, cache_write=0, output=0, complete=True, error=None)
        )
        attempt.update(
            ended=now(),
            duration=duration,
            status=status,
            response=self.memory.artifacts.json(response),
            raw_usage=raw_usage,
            usage=usage,
            error=error,
            cost=charge(usage, self.config.pricing) if attempt["kind"] == "model" else 0,
        )
        await self.memory.save("attempt", attempt)
        await self.memory.event(attempt["run_id"], "attempt", attempt, attempt["id"])
        self.log_call(attempt, response)
        return attempt

    async def metrics(self, run_id):
        run = await self.memory.run(run_id)
        attempts = await self.memory.records(run_id, "attempt", limit=100000)
        agent = [a for a in attempts if a["phase"] != "external_grader"]
        models = [a for a in agent if a["kind"] == "model"]
        events = await self.memory.all_events(run_id)
        comparisons = []
        for event in events:
            if event["kind"] != "selection":
                continue
            plan = event["plan"]
            owned = [a for a in agent if a["operation_id"] == plan["operation_id"]]
            executed = [a for a in owned if a["phase"] == "execution"]
            estimate = plan["estimate"]
            operation = await self.memory.get(plan["operation_id"], run_id)
            integrated = next(
                (
                    e["timestamp"]
                    for e in events
                    if e["kind"] == "integration_complete" and e["operation_id"] == operation["id"]
                ),
                None,
            )
            end = integrated or operation.get("ended")
            operation_wall = (
                (
                    datetime.fromisoformat(end) - datetime.fromisoformat(operation["created"])
                ).total_seconds()
                if end
                else None
            )
            comparisons.append(
                dict(
                    operation_id=plan["operation_id"],
                    mode=plan["mode"],
                    view=plan["view"],
                    predicted=estimate,
                    actual_inclusive_wall_seconds=operation_wall,
                    latency_error=operation_wall - estimate["latency"]
                    if operation_wall is not None
                    else None,
                    actual_execution_cost=sum(a["cost"] or 0 for a in executed),
                    actual_inclusive_cost=sum(a["cost"] or 0 for a in owned),
                    actual_execution_seconds=sum(a["duration"] or 0 for a in executed),
                    actual_inclusive_attempt_seconds=sum(a["duration"] or 0 for a in owned),
                    cost_complete=all(a["cost"] is not None for a in owned),
                    cost_error=(sum(a["cost"] or 0 for a in owned) - estimate["cost"])
                    if estimate["basis"] in ("USD", "synthetic-USD")
                    and all(a["cost"] is not None for a in owned)
                    else None,
                )
            )
        elapsed = (
            (
                datetime.fromisoformat(run["ended"] or now())
                - datetime.fromisoformat(run["started"])
            ).total_seconds()
            if run["started"]
            else 0
        )
        return dict(
            run_id=run_id,
            status=run["status"],
            verification=run["verification"],
            synthetic=any(a["synthetic"] for a in models),
            attempts=len(agent),
            model_attempts=len(models),
            tool_attempts=len(agent) - len(models),
            known_cost=sum(a["cost"] or 0 for a in agent),
            cost_complete=all(a["cost"] is not None for a in agent),
            usage_complete=all(a["usage"]["complete"] for a in models),
            tokens={
                k: sum(a["usage"].get(k) or 0 for a in models)
                for k in ("uncached", "cached", "cache_write", "output")
            },
            wall_seconds=elapsed,
            comparisons=comparisons,
            external_grader_cost=sum(
                a["cost"] or 0 for a in attempts if a["phase"] == "external_grader"
            ),
            overhead_seconds=sum(
                e.get("duration", 0) for e in events if e["kind"] in ("planning", "assembly")
            ),
        )


async def build_profiles(memory, run_ids):
    groups = defaultdict(list)
    for run_id in run_ids:
        run = await memory.run(run_id)
        if run["status"] != "succeeded":
            continue
        attempts = await memory.records(run_id, "attempt", limit=100000)
        for event in await memory.all_events(run_id):
            if event["kind"] != "selection":
                continue
            plan = event["plan"]
            op = await memory.get(plan["operation_id"], run_id)
            owned = [a for a in attempts if a["operation_id"] == op["id"]]
            calls = [
                a
                for a in owned
                if a["kind"] == "model" and a["phase"] == "execution" and not a["retry_of"]
            ]
            key = (
                run["request"]["task"]["adapter"],
                op["kind"],
                plan["mode"],
                plan["view"] or "NONE",
                str(math.ceil(plan["estimate"]["tokens"] / 2048)),
                run["fingerprint"],
            )
            cache_samples = []
            for call in calls:
                if not call["usage"]["complete"]:
                    continue
                request = memory.artifacts.read_json(call["request"])
                body = request["body"]
                prefix = digest([body["messages"][:1], body["tools"]])
                total = call["usage"]["uncached"] + call["usage"]["cached"]
                cache_samples.append((prefix, call["usage"]["cached"] / max(total, 1)))
            groups[key].append(
                dict(
                    cache_samples=cache_samples,
                    calls=len(calls),
                    output=sum(a["usage"]["output"] or 0 for a in calls),
                    latency=sum(a["duration"] or 0 for a in owned),
                    extra=sum(a["cost"] or 0 for a in owned if a["retry_of"]),
                    retry=any(a["retry_of"] for a in owned),
                    synthetic=any(a["synthetic"] for a in owned),
                    complete=all(a["usage"]["complete"] for a in calls),
                )
            )
    profiles = []
    for key, samples in groups.items():
        prefix_samples = defaultdict(list)
        for sample in samples:
            for prefix, fraction in sample["cache_samples"]:
                prefix_samples[prefix].append(fraction)
        profiles.append(
            dict(
                prefixes={
                    k: {"samples": len(v), "fraction": statistics.mean(v)}
                    for k, v in prefix_samples.items()
                },
                key=list(key),
                samples=len(samples),
                calls=statistics.mean(s["calls"] for s in samples),
                output=statistics.mean(s["output"] for s in samples),
                latency=statistics.mean(s["latency"] for s in samples),
                dispersion=statistics.pstdev(s["latency"] for s in samples),
                additional_frequency=statistics.mean(s["retry"] for s in samples),
                additional_cost=statistics.mean(s["extra"] for s in samples if s["retry"])
                if any(s["retry"] for s in samples)
                else 0,
                synthetic=any(s["synthetic"] for s in samples),
                usage_complete=all(s["complete"] for s in samples),
            )
        )
    return dict(revision="measured-v1", calibration_runs=run_ids, profiles=profiles)
