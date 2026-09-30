"""What every gateway route shares, whatever the provider's wire format.

Why it exists: the gateway serves more than one API format (OpenAI Chat Completions,
Gemini generateContent). Settings, caps, timing, recording and relaying a reply are
the same for all of them, so they live here once; gateway.py keeps only routing and
what is particular to each format.
What it must never do: log the key, or fail a call because EconoContext failed.
"""

import json
import logging
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler

from econocontext.store.db import AgentDB

from . import HOME

log = logging.getLogger("econocontext.gateway")
LOG_DIR = HOME / "logs" / "gateway"


def env(name: str, default: str | None = None) -> str | None:
    """From the process environment, else from HOME/.env. Never printed."""
    if name in os.environ:
        return os.environ[name]
    path = HOME / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"')
    return default


KEY = env("AGENT_PLATFORM_API_KEY")
MAX_CALLS_PER_RUN = int(env("ECONO_MAX_CALLS_PER_RUN", "60"))
MAX_INPUT_TOKENS_PER_DAY = int(env("ECONO_MAX_INPUT_TOKENS_PER_DAY", "3000000"))
LOG_BODIES = env("ECONO_GATEWAY_LOG_BODIES", "0") == "1"  # full requests, for fixtures only


def over_cap(db: AgentDB, run_id: str, run_limit: int | None = None) -> str | None:
    """Why this call must be refused, or None. `run_limit` is the run's own call cap
    (config limits.max_model_calls), never above the gateway's."""
    limit = min(MAX_CALLS_PER_RUN, run_limit or MAX_CALLS_PER_RUN)
    # Calls started (a model span is opened before forwarding), not just calls recorded:
    # usage is recorded after the reply, so a quick next call would otherwise slip past.
    calls = max(db.rows("SELECT COUNT(*) n FROM runtime_spans WHERE run_id=? AND kind='model'",
                        (run_id,))[0]["n"],
                db.rows("SELECT COUNT(*) n FROM outcomes WHERE run_id=?", (run_id,))[0]["n"])
    if calls >= limit:
        return f"run {run_id} reached {limit} model calls"
    today = datetime.now(timezone.utc).date().isoformat()
    used = db.rows("SELECT COALESCE(SUM(COALESCE(uncached_input,0)+COALESCE(cache_read,0)),0) n "
                   "FROM outcomes WHERE created_at >= ?", (today,))[0]["n"]
    if used >= MAX_INPUT_TOKENS_PER_DAY:
        return f"today's input tokens reached {MAX_INPUT_TOKENS_PER_DAY}"
    return None


class Handler(BaseHTTPRequestHandler):
    """HTTP plumbing and bookkeeping shared by every route."""
    protocol_version = "HTTP/1.1"
    db: AgentDB  # set in gateway.main()

    def log_message(self, fmt, *args):  # quiet: calls are logged as JSONL instead
        pass

    def reply_error(self, status: int, message: str) -> None:
        body = json.dumps({"error": {"message": f"econocontext gateway: {message}",
                                     "type": "econocontext_gateway"}}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def relay(self, url: str, raw: bytes, headers: dict, stream: bool,
              original: bytes | None = None) -> tuple[int, list[dict]]:
        """Send upstream; relay the reply to the harness unchanged (a stream line by line).
        Returns the status and the reply's JSON payloads: the body, or each SSE data
        chunk. Payloads are parsed only after they were relayed, and only on success.
        `original`: the harness's own request, when `raw` is a changed one. If the provider
        rejects the change (4xx), the original is sent instead, and `self.rejected` says so."""
        self.rejected = None
        try:
            upstream = urllib.request.urlopen(urllib.request.Request(url, raw, headers), timeout=600)
        except urllib.error.HTTPError as err:
            if original is not None and 400 <= err.code < 500:
                rejected = {"status": err.code, "error": err.read()[:500].decode(errors="replace")}
                log.warning("provider rejected a changed request (%s); sending the original", err.code)
                result = self.relay(url, original, headers, stream)
                self.rejected = rejected  # after the resend, which starts by clearing it
                return result
            upstream = err
        status = upstream.status if hasattr(upstream, "status") else upstream.code
        self.send_response(status)
        self.send_header("Content-Type", upstream.headers.get("Content-Type", "application/json"))
        if not stream or status != 200:
            data = upstream.read()
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            try:
                return status, [json.loads(data)] if status == 200 and data else []
            except ValueError:  # a passed-through reply need not be JSON
                return status, []
        self.send_header("Connection", "close")
        self.end_headers()
        payloads = []
        for line in upstream:
            self.wfile.write(line)
            self.wfile.flush()
            if line.startswith(b"data: {"):
                try:
                    payloads.append(json.loads(line[6:]))
                except ValueError:
                    log.warning("unparsable stream chunk")
        self.close_connection = True
        return status, payloads

    def measure(self, engine, agent, call_id, decision_id, usage, model=None) -> None:
        """Record what the provider reported (priced by the ledger, by the model the call
        used); end the turn."""
        try:
            engine.record(agent, decision_id, usage, call_id, model=model)
            engine.on_turn_end(agent)
        except Exception:
            log.exception("record failed")

    def start_span(self, engine, agent, call_id, model, decision_id, metadata) -> str | None:
        """Timing for this model call (runtime_spans). Never blocks the call."""
        span_id = f"{engine.run_id}:model:{call_id}"
        try:
            engine.start_span(span_id, agent, "model", str(model or "model"),
                              native_id=call_id, decision_id=decision_id, metadata=metadata)
            return span_id
        except Exception:
            log.exception("could not start the model span")
            return None

    def finish_span(self, engine, agent, span_id, latency, ok, metadata=None) -> None:
        if span_id is None:
            return
        try:
            engine.finish_span(span_id, agent, latency, "completed" if ok else "failed", metadata)
        except Exception:
            log.exception("could not finish the model span")

    def write_log(self, entry: dict) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_DIR / "calls.jsonl", "a") as f:
            f.write(json.dumps(entry) + "\n")

    def save_body(self, raw: bytes) -> None:
        if LOG_BODIES:
            (LOG_DIR / "bodies").mkdir(parents=True, exist_ok=True)
            (LOG_DIR / "bodies" / f"{time.time_ns()}.json").write_bytes(raw)
