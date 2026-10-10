"""A belief about what the provider has cached for each agent.

Why it exists: providers report cache hits after a call but do not reveal what is
cached now. A belief predicts hits before a call and is corrected from the
reported numbers after it, so the prediction's accuracy is measured from day one.
What it must never do: be used as a measurement. The ledger uses reported numbers.

PLACEHOLDER: the whole predictor. This version predicts the longest common prefix
(by segment content hash) with the agent's previous request, if that request was
sent within the TTL and the prefix is at least the provider's minimum cacheable
size; otherwise 0. The prediction is logged next to the reported value but is NOT
used by the cost model yet. The future version learns per-provider hit rates,
block granularity and eviction from the logged predicted-vs-reported pairs.
"""

import time

from ..types import CacheState, Segment


class CacheBelief:
    def __init__(self, ttl_seconds: float | None, min_cacheable_tokens: int | None):
        self.ttl = ttl_seconds
        self.min_tokens = min_cacheable_tokens or 0
        self.states: dict[str, CacheState] = {}

    def state(self, agent_id: str) -> CacheState:
        return self.states.setdefault(agent_id, CacheState(ttl_seconds=self.ttl))

    # PLACEHOLDER: longest-common-prefix belief; not used by the cost model yet (see module doc).
    def predict(self, agent_id: str, segments: list[Segment], now: float | None = None) -> int:
        """Predicted cached tokens for a request made of `segments`, sent now."""
        s = self.state(agent_id)
        now = time.time() if now is None else now
        if s.last_sent_at is None or (self.ttl is not None and now - s.last_sent_at > self.ttl):
            return 0
        shared = 0
        for i, segment in enumerate(segments):
            if i >= len(s.last_prefix_hashes) or s.last_prefix_hashes[i] != segment.content_hash:
                break
            shared += segment.tokens
        return shared if shared >= self.min_tokens else 0

    def sent(self, agent_id: str, segments: list[Segment], now: float | None = None) -> None:
        """Remember what was sent, as the prefix the next prediction compares against."""
        s = self.state(agent_id)
        s.last_prefix_hashes = [seg.content_hash for seg in segments]
        s.last_prefix_tokens = [seg.tokens for seg in segments]
        s.last_sent_at = time.time() if now is None else now

    def correct(self, agent_id: str, predicted: int | None, reported: int | None) -> None:
        """Fold a reported cache read into the running ratio of reported to predicted."""
        if predicted is None or reported is None:
            return
        s = self.state(agent_id)
        s.predicted_total += predicted
        s.reported_total += reported
        if s.predicted_total:
            s.observed_hit_ratio = s.reported_total / s.predicted_total
