"""Passive model-call capture shared by the gateway and auxiliary predictors."""

import json
from datetime import datetime, timezone

from ..observation import observe
from ..pricing.ledger import cost
from .stream import StreamResponse


class CallCapture:
    def __init__(self, sink, call_id, raw, body, *, agent_id=None, provider=None,
                 purpose="agent", identity_basis="runtime", source="gateway"):
        self.sink, self.call_id, self.agent_id = sink, call_id, agent_id
        self.source = source
        self.model = body.get("model")
        self.day = datetime.now(timezone.utc).date()
        self.stream = None
        self.chunk_index = 0
        self.response_complete = False
        self.http_status = None
        self.response_usage = None
        self.links = dict(call_id=call_id, agent_id=agent_id, source=source)
        observe(sink, "model.request", dict(raw=raw, body=body, purpose=purpose,
                provider=provider, identity_basis=identity_basis), **self.links)

    def sent(self, raw, body, decision_id=None):
        observe(self.sink, "model.sent", dict(raw=raw, body=body),
                decision_id=decision_id, **self.links)

    def chunk(self, raw):
        if self.sink is None:
            return
        observe(self.sink, "model.chunk", dict(index=self.chunk_index, raw=raw), **self.links)
        self.chunk_index += 1
        if self.stream is None:
            self.stream = StreamResponse()
        try:
            self.stream.feed(raw)
        except Exception:
            self.stream.invalid = True

    def response(self, raw):
        if self.sink is None:
            return
        try:
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise ValueError("Expected a response object")
            self.response_complete = True
            self.response_usage = body.get("usage")
        except (ValueError, TypeError):
            body = None
        observe(self.sink, "model.response", dict(raw=raw, body=body), **self.links)

    def finish(self, status, http_status=None, duration_ms=None, raw_usage=None,
               usage=None, card=None, error=None, normalizer=None):
        if self.sink is None:
            return
        try:
            if self.stream is not None:
                assembled = self.stream.body()
                observe(self.sink, "model.response", dict(raw=json.dumps(assembled).encode(),
                        body=assembled, assembled_from_sse=True), **self.links)
                self.response_complete = self.stream.done and not self.stream.invalid
                self.response_usage = assembled.get("usage")
            raw_usage = raw_usage if raw_usage is not None else self.response_usage
            nu, usd, complete, period = None, None, False, None
            try:
                if normalizer:
                    usage = normalizer(raw_usage, duration_ms)
                # Never silently price a different model using the run's default card.
                if usage is not None and card is not None and self.model in (card.model, "google/" + card.model):
                    nu, usd, complete, period = cost(usage, card, self.day)
            except Exception as exc:
                observe(self.sink, "capture.issue", dict(code="usage_mapping_failed",
                        detail=type(exc).__name__), **self.links)
            observe(self.sink, "model.finished", dict(status=status,
                    http_status=self.http_status if self.http_status is not None else http_status,
                    duration_ms=duration_ms, response_complete=self.response_complete,
                    raw_usage=raw_usage, usage=usage, pricing=card, cost_nu=nu, cost_usd=usd,
                    cost_complete=complete, price_period=period, error=error), **self.links)
        except Exception as exc:
            observe(self.sink, "capture.issue", dict(code="response_processing_failed",
                    detail=type(exc).__name__), **self.links)
