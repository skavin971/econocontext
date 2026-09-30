"""The model-call tap: a local gateway in front of Vertex (Gemini).

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
Shared plumbing (settings, caps, timing, relaying) is in gateway_common.py.
What it must never do: listen beyond localhost, log the key, or fail a call because
EconoContext failed (every engine call is fail-open; only the caps refuse calls).

Run: .venv/bin/python -m omnigent_layer.gateway [--port 8787]
"""

import argparse
import json
import re
import time
import uuid
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer

from econocontext.store.db import AgentDB
from econocontext.types import HostRequest

from . import DB_PATH, agent_id, current_run, engine_for, gateway_common as common, wire
from .gateway_common import env, log

# /run/<id>/...: an explicit run. /current/...: the run the bench marked current (used by
# sub-agents, whose model URL comes from a global Omnigent provider and cannot name a run).
PATH = re.compile(r"^/(?:run/(?P<run>[\w.:-]+)|current)(?:/agent/(?P<agent>[\w.-]+))?/v1(?P<rest>/.*)$")
# Tool results the gateway replaced with pointers (COMMIT_PENDING), per agent: applied to
# every later request so the change is made once and the prefix stays stable after it.
POINTERS_TABLE = ("CREATE TABLE IF NOT EXISTS gateway_pointers (run_id TEXT, agent_id TEXT, "
                  "tool_call_id TEXT, text TEXT, PRIMARY KEY (run_id, agent_id, tool_call_id))")
UPSTREAM = (env("ECONOCONTEXT_BASE_URL") or "").rstrip("/")  # Vertex .../endpoints/openapi


class Gateway(common.Handler):

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
        run_id = match["run"] or current_run()
        body = json.loads(raw)
        # Named agents (workers) share one URL; each instance is told apart by its first
        # user message, which a continued worker keeps.
        agent = agent_id(run_id, match["agent"], wire.first_user_text(body))
        entry.update(run=run_id, agent=agent)
        found = engine_for(run_id) if run_id else None
        if found is None:
            entry["refused"] = "unregistered run"
            self.write_log(entry)
            return self.reply_error(400, f"run {run_id} is not registered")
        engine, arm = found
        cap = common.over_cap(self.db, run_id, engine.cfg["limits"].get("max_model_calls"))
        if cap:
            entry["refused"] = cap
            self.write_log(entry)
            return self.reply_error(429, cap)

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
        self.save_body(raw)

        call_id = uuid.uuid4().hex  # one id for the outcome row and its timing span
        span = self.start_span(engine, agent, call_id, body.get("model"), decision_id,
                               {"arm": arm, "stream": stream})
        usage, status = None, 502
        try:
            status, payloads = self.relay(UPSTREAM + "/chat/completions", raw,
                                          {"Content-Type": "application/json",
                                           "x-goog-api-key": common.KEY or ""}, stream)
            usage = next((p["usage"] for p in reversed(payloads) if p.get("usage")), None)
        finally:
            latency = (time.monotonic() - started) * 1000
            self.finish_span(engine, agent, span, latency, status == 200)
        entry.update(status=status, usage=usage, latency_ms=round(latency))
        self.write_log(entry)
        if status == 200:
            self.measure(engine, agent, call_id, decision_id, wire.to_usage(usage, latency))

    def plan(self, engine, run_id, agent, body) -> tuple[dict, str | None]:
        """plan_prompt on the full request. Observe mode logs; autopilot may reorder, and
        may point out old tool results (COMMIT_PENDING). A result pointed out once stays
        pointed out in every later request of this agent, so the prefix is stable again."""
        try:
            body = self.keep_pointers(run_id, agent, body)
            segments, index = wire.to_segments(run_id, agent, body)
            rendered = engine.plan_prompt(agent, HostRequest(agent, segments))
            if rendered.applied:
                body = {**body, "messages": wire.from_segments(rendered.segments, body, index,
                                                                 rendered.pointer_texts)}
                for s in rendered.segments:
                    if s.id in rendered.pointer_texts:
                        self.db.execute("INSERT OR REPLACE INTO gateway_pointers VALUES(?,?,?,?)",
                                        (run_id, agent, s.native_id, rendered.pointer_texts[s.id]))
            return body, rendered.decision_id
        except Exception:
            log.exception("plan_prompt failed; forwarding the harness's request")
            return body, None

    def keep_pointers(self, run_id, agent, body) -> dict:
        """Replace tool results this agent already had pointed out (by tool-call id)."""
        stored = {r["tool_call_id"]: r["text"] for r in self.db.rows(
            "SELECT tool_call_id, text FROM gateway_pointers WHERE run_id=? AND agent_id=?",
            (run_id, agent))}
        if not stored:
            return body
        return {**body, "messages": [
            {**m, "content": stored[m["tool_call_id"]]}
            if m.get("role") == "tool" and m.get("tool_call_id") in stored else m
            for m in body.get("messages", [])]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--port", type=int, default=8787)
    port = parser.parse_args().port
    if not UPSTREAM or not common.KEY:
        raise SystemExit("ECONOCONTEXT_BASE_URL and AGENT_PLATFORM_API_KEY must be set (.env)")
    Gateway.db = AgentDB(DB_PATH)
    Gateway.db.execute(POINTERS_TABLE)
    server = ThreadingHTTPServer(("127.0.0.1", port), Gateway)  # localhost only
    print(f"econocontext gateway on http://127.0.0.1:{port} -> {UPSTREAM}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
