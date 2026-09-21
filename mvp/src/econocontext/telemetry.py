"""Attempt accounting independent of the planner, also usable by external runners."""

import math
import statistics
from collections import defaultdict
from datetime import datetime

from .contracts import digest, now, uid


def normalize(raw):
    if raw is None:
        return dict(uncached=None, cached=None, output=None, complete=False, error=None)
    try:
        total, output = raw["prompt_tokens"], raw["completion_tokens"]
        cached = (raw.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
        cache_reported = "cached_tokens" in (raw.get("prompt_tokens_details") or {})
        if (
            any(
                isinstance(x, bool) or not isinstance(x, int) or x < 0
                for x in (total, cached, output)
            )
            or cached > total
        ):
            raise ValueError("Invalid disjoint token counts")
        # Completion tokens include reasoning tokens; never charge those again.
        return dict(
            uncached=total - cached,
            cached=cached,
            output=output,
            complete=True,
            cache_reported=cache_reported,
            error=None,
        )
    except (KeyError, ValueError, TypeError) as exc:
        return dict(uncached=None, cached=None, output=None, complete=False, error=str(exc))


def charge(usage, pricing):
    if not pricing or not usage["complete"]:
        return None
    if (
        not usage.get("cache_reported", False)
        and pricing.input_per_million != pricing.cached_per_million
    ):
        return None
    return (
        usage["uncached"] * pricing.input_per_million
        + usage["cached"] * pricing.cached_per_million
        + usage["output"] * pricing.output_per_million
    ) / 1_000_000


class Telemetry:
    def __init__(self, memory, config):
        self.memory, self.config = memory, config

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
            synthetic=self.config.backend == "scripted",
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
            else dict(uncached=0, cached=0, output=0, complete=True, error=None)
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
                k: sum(a["usage"][k] or 0 for a in models) for k in ("uncached", "cached", "output")
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
