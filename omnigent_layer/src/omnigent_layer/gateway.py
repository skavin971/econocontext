"""The model-call tap: a local gateway in front of Vertex (Gemini).

Why it exists: Omnigent's policies see only metadata about model calls. Every agent's
full prompt, and the provider's exact usage, pass through here instead. The harness
points its base URL at

    http://127.0.0.1:<port>/run/<run_id>/v1              the main agent (Chat Completions)
    http://127.0.0.1:<port>/run/<run_id>/agent/<name>/v1 a named sub-agent
    http://127.0.0.1:<port>/run/<run_id>/gemini          stock Gemini CLI (generateContent;
                                                         gemini_wire.py reads the format)
    http://127.0.0.1:<port>/run/<run_id>/anthropic       Claude Code (Anthropic Messages;
                                                         anthropic_wire.py reads the format)

and gets a placeholder key. The gateway adds the real key upstream, so the key never
reaches an agent.

Per call:  cap check -> plan_prompt (econo arm only) -> forward (timed as a runtime span)
           -> record usage (priced by the ledger) -> turn end.
Baseline runs are forwarded byte-for-byte and only measured. Gemini and Anthropic calls
are forwarded byte-for-byte (measured, not yet changed); a path the gateway does not know
(countTokens, embedContent, count_tokens, anything else) is passed through, never refused.
Anthropic calls also stop at a dollar budget: the run's own and the key's total.
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

from . import (DB_PATH, agent_id, anthropic_wire, current_run, engine_for,
               gateway_common as common, gemini_wire, observe, wire)
from .gateway_common import env, log

# /run/<id>/...: an explicit run. /current/...: the run the bench marked current (used by
# sub-agents, whose model URL comes from a global Omnigent provider and cannot name a run).
PATH = re.compile(r"^/(?:run/(?P<run>[\w.:-]+)|current)(?:/agent/(?P<agent>[\w.-]+))?/v1(?P<rest>/.*)$")
# Tool results the gateway replaced with pointers (COMMIT_PENDING), per agent: applied to
# every later request so the change is made once and the prefix stays stable after it.
POINTERS_TABLE = ("CREATE TABLE IF NOT EXISTS gateway_pointers (run_id TEXT, agent_id TEXT, "
                  "tool_call_id TEXT, text TEXT, PRIMARY KEY (run_id, agent_id, tool_call_id))")
UPSTREAM = (env("ECONOCONTEXT_BASE_URL") or "").rstrip("/")  # Vertex .../endpoints/openapi
# A provider's own format: /run/<id>/<provider>/<the path the harness built>[?query]
PROVIDER_PATH = re.compile(r"^/(?:run/(?P<run>[\w.:-]+)|current)/(?P<provider>gemini|anthropic)"
                           r"(?P<rest>/[^?]*)(?:\?(?P<query>.*))?$")
GEMINI_UPSTREAM = (env("ECONOCONTEXT_GEMINI_UPSTREAM") or "https://aiplatform.googleapis.com").rstrip("/")
ANTHROPIC_UPSTREAM = (env("ECONOCONTEXT_ANTHROPIC_UPSTREAM") or "https://api.anthropic.com").rstrip("/")
# Request headers not passed upstream: credentials (replaced), our run id, and what the
# HTTP client sets itself. Encoding is not passed so replies stay readable.
DROP_HEADERS = {"host", "content-length", "connection", "accept-encoding", "authorization",
                "x-goog-api-key", "x-api-key", "x-econo-run-id"}
# The Anthropic key's total dollar budget, across every run that uses it.
ANTHROPIC_BUDGET_USD = float(env("ECONO_ANTHROPIC_BUDGET_USD", "4.5"))
# The only models the Anthropic route may call (a harness can ask for others, e.g. Claude
# Code's Agent tool takes model: "fable"). Anything else is refused, never sent.
ANTHROPIC_MODELS = set((env("ECONO_ANTHROPIC_MODELS") or "claude-sonnet-5").split(","))


def gemini_key() -> str:
    """The real Gemini credential: its own setting, else the Vertex key. Never logged."""
    return env("ECONOCONTEXT_GEMINI_UPSTREAM_KEY") or common.KEY or ""


def anthropic_key() -> str:
    """The real Anthropic credential. Held only here; never logged, never sent to an agent."""
    return env("ECONOCONTEXT_ANTHROPIC_KEY") or ""


# provider -> (its wire module, upstream base URL, the real credential header)
PROVIDERS = {
    "gemini": (gemini_wire, lambda: GEMINI_UPSTREAM, lambda: {"x-goog-api-key": gemini_key()}),
    "anthropic": (anthropic_wire, lambda: ANTHROPIC_UPSTREAM, lambda: {"x-api-key": anthropic_key()}),
}


def over_budget(db: AgentDB, run_id: str, run_usd: float | None) -> str | None:
    """Why an Anthropic call must be refused for money, or None: the run's own budget
    (config limits.per_instance_budget_usd), and the key's total (ECONO_ANTHROPIC_BUDGET_USD)
    over every run on the anthropic route."""
    spent = db.rows("SELECT COALESCE(SUM(cost_usd), 0) n FROM outcomes WHERE run_id=?",
                    (run_id,))[0]["n"]
    if run_usd is not None and spent >= run_usd:
        return f"run {run_id} spent ${spent:.4f} of its ${run_usd:.2f} budget"
    total = db.rows("SELECT COALESCE(SUM(o.cost_usd), 0) n FROM outcomes o JOIN runs r "
                    "ON r.run_id = o.run_id WHERE r.host LIKE '%:claude-code'", ())[0]["n"]
    if total >= ANTHROPIC_BUDGET_USD:
        return f"the Anthropic key's ${ANTHROPIC_BUDGET_USD:.2f} budget is spent (${total:.4f})"
    return None


class Gateway(common.Handler):

    def do_GET(self):
        provider = PROVIDER_PATH.match(self.path)
        if provider:
            return self.provider_call(provider, None, time.monotonic())
        self.reply_error(404, f"only POST /run/<run_id>[/agent/<name>]/v1/chat/completions "
                              f"is served (got GET {self.path})")

    def do_POST(self):
        started = time.monotonic()
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        provider = PROVIDER_PATH.match(self.path)
        if provider:
            return self.provider_call(provider, raw, started)
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

    def provider_call(self, match, raw: bytes | None, started: float) -> None:
        """A request in a provider's own format (Gemini CLI, Claude Code): forwarded
        unchanged, with the real credential; model calls are capped, timed, recorded and
        observed, everything else is only passed through and logged."""
        wire_module, upstream, credential = PROVIDERS[match["provider"]]
        run_id = match["run"] or self.headers.get("X-Econo-Run-ID") or current_run()
        rest, query = match["rest"], match["query"]
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            body = {}
        call = wire_module.model_request(rest, body)
        entry = {"at": datetime.now(timezone.utc).isoformat(), "path": rest, "run": run_id,
                 "format": match["provider"]}
        found = engine_for(run_id) if run_id else None
        if found is None:
            entry["refused"] = "unregistered run"
            self.write_log(entry)
            return self.reply_error(400, f"run {run_id} is not registered")
        engine, arm = found
        params = [kv for kv in (query or "").split("&") if kv and not kv.startswith("key=")]
        url = upstream() + rest + ("?" + "&".join(params) if params else "")
        headers = {k: v for k, v in self.headers.items() if k.lower() not in DROP_HEADERS}
        headers.update(credential())
        if call is None:  # not a model call: countTokens, count_tokens, unknown paths
            status, _ = self.relay(url, raw, headers, stream=False)
            entry.update(status=status, passed_through=True)
            return self.write_log(entry)
        model, stream = call
        cap = common.over_cap(self.db, run_id, engine.cfg["limits"].get("max_model_calls"))
        if not cap and match["provider"] == "anthropic" and model not in ANTHROPIC_MODELS:
            cap = f"model {model} is not allowed on this route (ECONO_ANTHROPIC_MODELS)"
        if not cap and match["provider"] == "anthropic":
            cap = over_budget(self.db, run_id, engine.cfg["limits"].get("per_instance_budget_usd"))
        if cap:
            entry["refused"] = cap
            self.write_log(entry)
            return self.reply_error(429, cap)
        agent = agent_id(run_id, None)  # sub-agents are told apart by context_key, not id
        entry.update(arm=arm, model=model, stream=stream, request_bytes=len(raw or b""))
        self.save_body(raw or b"")
        call_id = uuid.uuid4().hex
        observed = self.observe(wire_module, engine, arm, agent, run_id, raw, body)
        decision_id, sent = None, raw
        if arm == "econo" and hasattr(wire_module, "to_segments"):
            decision_id, sent = self.plan_provider(engine, run_id, agent, wire_module, body, raw,
                                                   plan="retry_of" not in observed)
        changed = sent is not raw
        span = self.start_span(engine, agent, call_id, model, decision_id,
                               {"arm": arm, "stream": stream, "format": match["provider"],
                                "changed": changed, **observed})
        status, payloads = 502, []
        try:
            status, payloads = self.relay(url, sent, headers, stream,
                                          original=raw if changed else None)
            if self.rejected:  # the provider refused the change: stop changing this run
                self.stop_changes(run_id, agent, self.rejected)
        finally:
            latency = (time.monotonic() - started) * 1000
            self.finish_span(engine, agent, span, latency, status == 200,
                             wire_module.observe_response(payloads) if payloads else None)
        usage = wire_module.extract_usage(payloads)
        entry.update(status=status, usage=usage, latency_ms=round(latency))
        self.write_log(entry)
        if status == 200:
            self.measure(engine, agent, call_id, decision_id, wire_module.to_usage(usage, latency),
                         model)

    def plan_provider(self, engine, run_id, agent, wire_module, body, raw, plan=True):
        """plan_prompt on a provider-format request (Claude Code). Returns the decision id and
        the bytes to send. Observe mode: the harness's request, unchanged. Autopilot: the
        one change this route makes is COMMIT_PENDING, an old tool result replaced by a
        pointer; a result pointed out once stays pointed out in every later request (the
        prefix stays stable), and a run whose change the provider refused gets no more.
        Reordering (ZONED) and retrieval are never carried out here. Fails open."""
        try:
            stopped = self.db.rows("SELECT 1 FROM gateway_pointers WHERE run_id=? AND agent_id=? "
                                   "AND tool_call_id='*stopped*'", (run_id, agent))
            pointers = {} if stopped else {r["tool_call_id"]: r["text"] for r in self.db.rows(
                "SELECT tool_call_id, text FROM gateway_pointers WHERE run_id=? AND agent_id=?",
                (run_id, agent))}
            body = wire_module.with_pointers(body, pointers)
            decision_id = None
            if plan:
                rendered = engine.plan_prompt(agent, HostRequest(
                    agent, wire_module.to_segments(run_id, agent, body)))
                decision_id = rendered.decision_id
                new = {s.native_id: rendered.pointer_texts[s.id] for s in rendered.segments
                       if s.id in (rendered.pointer_texts or {})} if rendered.applied and not stopped else {}
                for use_id, text in new.items():
                    self.db.execute("INSERT OR REPLACE INTO gateway_pointers VALUES(?,?,?,?)",
                                    (run_id, agent, use_id, text))
                body = wire_module.with_pointers(body, new)
                pointers = {**pointers, **new}
            return decision_id, (wire_module.encode(body) if pointers else raw)
        except Exception:
            log.exception("plan_prompt failed; the request is sent unchanged")
            return None, raw

    def stop_changes(self, run_id, agent, rejected: dict) -> None:
        """The provider refused a changed request: forget this agent's pointers, change
        nothing more in this run, and say why in the log."""
        self.db.execute("DELETE FROM gateway_pointers WHERE run_id=? AND agent_id=?", (run_id, agent))
        self.db.execute("INSERT OR REPLACE INTO gateway_pointers VALUES(?,?,?,?)",
                        (run_id, agent, "*stopped*", json.dumps(rejected)))
        self.write_log({"at": datetime.now(timezone.utc).isoformat(), "run": run_id,
                        "autopilot_stopped": rejected})

    def observe(self, wire_module, engine, arm: str, agent: str, run_id: str,
                raw: bytes | None, body: dict) -> dict:
        """What this request carried (the wire's observe_request) and its call
        number in the run; in the econo arm also the evidence it brought in. A request
        identical to an earlier one is the harness retrying that call: marked `retry_of`,
        and its evidence is not counted again. Never blocks the call: a body it cannot
        read is still sent."""
        try:
            seen = wire_module.observe_request(body, raw or b"")
            call_no = self.db.rows("SELECT COUNT(*) n FROM runtime_spans WHERE run_id=? AND "
                                   "kind='model'", (run_id,))[0]["n"]
            earlier = self.db.rows(
                "SELECT json_extract(metadata, '$.call_no') n FROM runtime_spans WHERE run_id=? "
                "AND kind='model' AND json_extract(metadata, '$.request_hash')=? LIMIT 1",
                (run_id, seen["request_hash"]))
            if earlier:
                return {"call_no": call_no, "retry_of": earlier[0]["n"], **seen}
            if arm == "econo":
                engine.observe_evidence(agent, call_no, observe.evidence_events(
                    wire_module.tool_results(body), wire_module.TOOLS, engine.workdir,
                    self.db.mutations(run_id)))
            return {"call_no": call_no, **seen}
        except Exception:
            log.exception("could not read the request")
            return {}

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
