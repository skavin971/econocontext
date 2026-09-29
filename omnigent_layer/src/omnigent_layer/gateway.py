"""The model-call tap: a local OpenAI-compatible gateway in front of Vertex (Gemini).

Why it exists: Omnigent's policies see only metadata about model calls. Every agent's
full prompt, and the provider's exact usage, pass through here instead. The harness
points its base URL at

    http://127.0.0.1:<port>/run/<run_id>/v1              the main agent
    http://127.0.0.1:<port>/run/<run_id>/agent/<name>/v1 a named sub-agent

and gets a placeholder key. The gateway adds the real key upstream, so the key never
reaches an agent.

Per call:  cap check -> plan_prompt (econo arm only) -> forward (timed as a runtime span)
           -> record usage (priced by the ledger) -> turn end.
Baseline runs are forwarded byte-for-byte and only measured.
What it must never do: listen beyond localhost, log the key, or fail a call because
EconoContext failed (every engine call is fail-open; only the caps refuse calls).

Run: .venv/bin/python -m omnigent_layer.gateway [--port 8787]
"""

import argparse
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from econocontext.store.db import AgentDB
from econocontext.types import HostRequest

from . import DB_PATH, HOME, agent_id, engine_for, wire

log = logging.getLogger("econocontext.gateway")
PATH = re.compile(r"^/run/(?P<run>[\w.:-]+)(?:/agent/(?P<agent>[\w.-]+))?/v1(?P<rest>/.*)$")
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


UPSTREAM = (env("ECONOCONTEXT_BASE_URL") or "").rstrip("/")  # Vertex .../endpoints/openapi
KEY = env("AGENT_PLATFORM_API_KEY")
MAX_CALLS_PER_RUN = int(env("ECONO_MAX_CALLS_PER_RUN", "60"))
MAX_INPUT_TOKENS_PER_DAY = int(env("ECONO_MAX_INPUT_TOKENS_PER_DAY", "3000000"))
LOG_BODIES = env("ECONO_GATEWAY_LOG_BODIES", "0") == "1"  # full requests, for the spike only


def over_cap(db: AgentDB, run_id: str) -> str | None:
    # Calls started (a model span is opened before forwarding), not just calls recorded:
    # usage is recorded after the reply, so a quick next call would otherwise slip past.
    calls = max(db.rows("SELECT COUNT(*) n FROM runtime_spans WHERE run_id=? AND kind='model'",
                        (run_id,))[0]["n"],
                db.rows("SELECT COUNT(*) n FROM outcomes WHERE run_id=?", (run_id,))[0]["n"])
    if calls >= MAX_CALLS_PER_RUN:
        return f"run {run_id} reached {MAX_CALLS_PER_RUN} model calls"
    today = datetime.now(timezone.utc).date().isoformat()
    used = db.rows("SELECT COALESCE(SUM(COALESCE(uncached_input,0)+COALESCE(cache_read,0)),0) n "
                   "FROM outcomes WHERE created_at >= ?", (today,))[0]["n"]
    if used >= MAX_INPUT_TOKENS_PER_DAY:
        return f"today's input tokens reached {MAX_INPUT_TOKENS_PER_DAY}"
    return None


class Gateway(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    db: AgentDB  # set in main()

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

    def do_GET(self):
        self.reply_error(404, f"only POST /run/<run_id>[/agent/<name>]/v1/chat/completions "
                              f"is served (got GET {self.path})")

    def do_POST(self):
        started = time.monotonic()
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        match = PATH.match(self.path)
        entry = {"at": datetime.now(timezone.utc).isoformat(), "path": self.path}
        if not match or match["rest"] != "/chat/completions":
            # e.g. /responses: Vertex serves Chat Completions only (question 0a).
            entry["refused"] = "unsupported path"
            self.write_log(entry)
            return self.reply_error(404, f"unsupported path {self.path}: this gateway serves "
                                         f"Chat Completions only; set the harness to use them")
        run_id, agent = match["run"], agent_id(match["run"], match["agent"])
        entry.update(run=run_id, agent=agent)
        found = engine_for(run_id)
        if found is None:
            entry["refused"] = "unregistered run"
            self.write_log(entry)
            return self.reply_error(400, f"run {run_id} is not registered")
        engine, arm = found
        cap = over_cap(self.db, run_id)
        if cap:
            entry["refused"] = cap
            self.write_log(entry)
            return self.reply_error(429, cap)

        body = json.loads(raw)
        decision_id = None
        if arm == "econo":
            body, decision_id = self.plan(engine, run_id, agent, body)
            raw = json.dumps(body).encode()
        stream = bool(body.get("stream"))
        if stream and not (body.get("stream_options") or {}).get("include_usage"):
            # Measurement plumbing, both arms: ask for the usage chunk at the end of the stream.
            body["stream_options"] = {**(body.get("stream_options") or {}), "include_usage": True}
            raw = json.dumps(body).encode()
        entry.update(arm=arm, stream=stream, model=body.get("model"),
                     messages=len(body.get("messages", [])), tools=len(body.get("tools") or []))
        if LOG_BODIES:
            (LOG_DIR / "bodies").mkdir(parents=True, exist_ok=True)
            (LOG_DIR / "bodies" / f"{time.time_ns()}.json").write_bytes(raw)

        call_id = uuid.uuid4().hex  # one id for the outcome row and its timing span
        span = self.start_span(engine, agent, call_id, body, decision_id, arm)
        usage, status = None, 502
        try:
            usage, status = self.forward(raw, stream)
        finally:
            latency = (time.monotonic() - started) * 1000
            self.finish_span(engine, agent, span, latency, status == 200)
        entry.update(status=status, usage=usage, latency_ms=round(latency))
        self.write_log(entry)
        if status == 200:
            self.measure(engine, agent, call_id, decision_id, usage, latency)

    def plan(self, engine, run_id, agent, body) -> tuple[dict, str | None]:
        """plan_prompt on the full request. Observe mode logs; autopilot may reorder."""
        try:
            segments, index = wire.to_segments(run_id, agent, body)
            rendered = engine.plan_prompt(agent, HostRequest(agent, segments))
            if rendered.applied:
                body = {**body, "messages": wire.from_segments(rendered.segments, body, index)}
            return body, rendered.decision_id
        except Exception:
            log.exception("plan_prompt failed; forwarding the harness's request")
            return body, None

    def measure(self, engine, agent, call_id, decision_id, usage, latency) -> None:
        try:
            engine.record(agent, decision_id, wire.to_usage(usage, latency), call_id)
            engine.on_turn_end(agent)
        except Exception:
            log.exception("record failed")

    def start_span(self, engine, agent, call_id, body, decision_id, arm) -> str | None:
        """Timing for this model call (runtime_spans). Never blocks the call."""
        span_id = f"{engine.run_id}:model:{call_id}"
        try:
            engine.start_span(span_id, agent, "model", str(body.get("model") or "model"),
                              native_id=call_id, decision_id=decision_id,
                              metadata={"arm": arm, "stream": bool(body.get("stream"))})
            return span_id
        except Exception:
            log.exception("could not start the model span")
            return None

    def finish_span(self, engine, agent, span_id, latency, ok) -> None:
        if span_id is None:
            return
        try:
            engine.finish_span(span_id, agent, latency, "completed" if ok else "failed")
        except Exception:
            log.exception("could not finish the model span")

    def forward(self, raw: bytes, stream: bool) -> tuple[dict | None, int]:
        """Send upstream with the real key; relay the reply (streamed line by line)."""
        request = urllib.request.Request(
            UPSTREAM + "/chat/completions", raw,
            {"Content-Type": "application/json", "x-goog-api-key": KEY or ""})
        try:
            upstream = urllib.request.urlopen(request, timeout=600)
        except urllib.error.HTTPError as err:
            upstream = err
        status = upstream.status if hasattr(upstream, "status") else upstream.code
        self.send_response(status)
        self.send_header("Content-Type", upstream.headers.get("Content-Type", "application/json"))
        usage = None
        if not stream or status != 200:
            data = upstream.read()
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            if status == 200:
                usage = json.loads(data).get("usage")
            return usage, status
        self.send_header("Connection", "close")
        self.end_headers()
        for line in upstream:
            self.wfile.write(line)
            self.wfile.flush()
            if line.startswith(b"data: {"):
                chunk = json.loads(line[6:])
                usage = chunk.get("usage") or usage
        self.close_connection = True
        return usage, status

    def write_log(self, entry: dict) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_DIR / "calls.jsonl", "a") as f:
            f.write(json.dumps(entry) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--port", type=int, default=8787)
    port = parser.parse_args().port
    if not UPSTREAM or not KEY:
        raise SystemExit("ECONOCONTEXT_BASE_URL and AGENT_PLATFORM_API_KEY must be set (.env)")
    Gateway.db = AgentDB(DB_PATH)
    server = ThreadingHTTPServer(("127.0.0.1", port), Gateway)  # localhost only
    print(f"econocontext gateway on http://127.0.0.1:{port} -> {UPSTREAM}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
