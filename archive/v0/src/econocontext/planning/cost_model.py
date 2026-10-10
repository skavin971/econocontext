import json
import math

from ..assembler import token_count
from ..contracts import CandidatePlan, Estimate, Mode, Operation
from ..state import ExecutionState


class CostModel:
    def __init__(self, config):
        self.config = config
        self.profiles = (
            json.loads(config.profile_path.read_text())
            if config.profile_path
            else {
                "revision": "synthetic-default-v1"
                if config.backend == "fake"
                else "conservative-default-v1",
                "profiles": [],
            }
        )

        if config.backend == "live" and (
            self.profiles.get("synthetic")
            or any(p.get("synthetic") for p in self.profiles["profiles"])
        ):
            raise ValueError("Synthetic calibration profiles cannot optimize live runs")
        for profile in self.profiles["profiles"] + list(self.profiles.get("defaults", {}).values()):
            for key in (
                "calls",
                "output",
                "latency",
                "samples",
                "dispersion",
                "additional_cost",
                "additional_frequency",
                "preparation_cost",
            ):
                if key in profile and (
                    not isinstance(profile[key], (float, int))
                    or not math.isfinite(profile[key])
                    or profile[key] < 0
                ):
                    raise ValueError(f"Invalid profile value: {key}")

    def estimate(
        self, operation: Operation, state: ExecutionState, candidate: CandidatePlan
    ) -> Estimate:
        size = state["candidate_tokens"][candidate.id]
        key = [
            state["adapter"].name,
            operation.kind,
            candidate.mode.value,
            candidate.view or "NONE",
            str(math.ceil(size / 2048)),
            state["fingerprint"],
        ]
        profile = next((p for p in self.profiles["profiles"] if p["key"] == key), {})
        # Injected profiles are keyed by properties, never fixture names or evidence IDs.
        profile = profile or self.profiles.get("defaults", {}).get(
            candidate.view or candidate.mode.value, {}
        )
        fresh = candidate.mode == Mode.FRESH
        calls = profile.get("calls", 2 if fresh else 3)
        output = profile.get("output", 256 * calls)
        if candidate.mode == Mode.REUSE:
            calls, output = 0, 0
        parent = candidate.mode != Mode.CONTINUE or candidate.worker_id != state["worker"].id
        parent_tokens = state["parent_tokens"] + operation.result_tokens
        preparation = profile.get("preparation_cost", 0)
        prefix = state.get("candidate_prefixes", {}).get(candidate.id)
        cache = profile.get("prefixes", {}).get(prefix, {})
        # Credit only the compatible invariant prefix, with measured samples.
        fraction = min(0.1, cache.get("fraction", 0)) if cache.get("samples", 0) >= 3 else 0
        fraction = min(fraction, state.get("prefix_tokens", {}).get(candidate.id, 0) / max(size, 1))
        retained = size * calls + output * max(calls - 1, 0) / 2
        pricing = self.config.pricing
        if pricing:
            execution = (
                retained
                * (
                    (1 - fraction) * pricing.input_per_million
                    + fraction * pricing.cached_per_million
                )
                + output * pricing.output_per_million
            ) / 1e6
            integration = (
                (
                    (parent_tokens * pricing.input_per_million + 256 * pricing.output_per_million)
                    / 1e6
                )
                if parent
                else 0
            )
            additional = profile.get("additional_frequency", 0.1) * profile.get(
                "additional_cost", execution / max(calls, 1)
            )
            basis = "synthetic-USD" if self.config.backend == "fake" else "USD"
        else:
            execution = retained + output
            integration = parent_tokens + 256 if parent else 0
            preparation, additional = 1, profile.get("additional_frequency", 0.1) * 256
            basis = "synthetic-token-units" if self.config.backend == "fake" else "fixed-fallback"
        breakdown = dict(
            preparation=preparation,
            execution=execution,
            integration=integration,
            additional=additional,
        )
        holding = self.holding(state, candidate)
        if holding is not None:
            breakdown["holding"] = holding
        latency = profile.get("latency", calls * 0.2 + (0.2 if parent else 0) + 0.01)
        return Estimate(
            cost=sum(breakdown.values()),
            latency=latency,
            breakdown=breakdown,
            uncertainty=profile.get("dispersion", 0.5),
            provenance=self.profiles["revision"],
            basis=basis,
            tokens=size,
            samples=profile.get("samples", 0),
        )

    def holding(self, state, candidate):
        """What the root pays, from now to its forecast end, for what this plan leaves in it.

        Answering inline leaves the whole result in the root's prefix. Any other
        plan answers with a bounded view plus a finding, capped at half of it
        (representation.excerpt_chars). Whatever stays is written to the cache
        once and read back on every later call, so a long remaining life favours
        leaving less -- and a short one does not repay a child's overhead.
        """
        prior, pricing = self.config.lifetime_prior, self.config.pricing
        observation = state.get("observation")
        if not prior or not pricing or not observation:
            return None
        inline = token_count(observation["inline"], 1.0)
        answered_inline = (
            candidate.mode == Mode.CONTINUE and candidate.worker_id == state["worker"].id
        )
        held = inline if answered_inline else inline / 2
        remaining = max(1, prior - state.get("root_calls", 0))
        write = pricing.cache_write_per_million
        write = pricing.input_per_million if write is None else write
        return held * (write + pricing.cached_per_million * remaining) / 1e6

    def render_estimate(self, messages, tools, limits):
        return token_count(
            dict(
                model=self.config.model,
                messages=messages,
                tools=tools,
                **{self.config.output_parameter: limits.output_tokens},
            ),
            limits.safety_margin,
        )
