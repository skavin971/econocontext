"""Measure-only gateway in front of Vertex's OpenAI-compatible endpoint (Gemini).

Trimmed from omnigent_layer/gateway.py + gateway_common.py on branch
feature/claude-code @ ae9fd5a. Kept: the OpenAI-style route, localhost binding,
key injection from the environment, streaming relay with include_usage, latency
timing, fail-open measurement. Dropped: the engine, plan_prompt, the Anthropic and
Gemini-native routes, COMMIT_PENDING, policies, call caps. Added: a spend cap, a
concurrency limit, and transparent retries of rate-limited calls.

Both arms point CLM's api_base at

    http://127.0.0.1:<port>/run/<run_id>/v1        (optionally /run/<id>/agent/<n>/v1)

and get a placeholder key. Per call:

    spend check -> wait for an in-flight slot -> forward the SAME bytes upstream
    (on 429/503: back off and resend the same bytes, up to RETRY_BUDGET_S)
    -> relay the reply -> record usage and cost in the ledger.

The request body is forwarded byte for byte. The one exception (both arms): a
streaming request that does not ask for usage gets stream_options.include_usage.

Why retry here: CLM retries a failed call only 5 times (with up to 60 s random
backoff) and then the trial crashes, which would look like a task failure caused
by Google's quota, not by the agent. The agent sees one call; the ledger records
the attempts and the time spent waiting, so wall time can be reported with and
without rate-limit waits. RETRY_BUDGET_S (240 s) + one attempt stays below CLM's
600 s call timeout, so CLM never resends a call the gateway is still retrying.

What it must never do: listen beyond 127.0.0.1, log or echo the key, change a
prompt, or fail a call because measuring failed.

Run:  python -m econoclm.core.gateway --ledger runs/<phase>/gateway.sqlite [--port 8787]
One ledger per phase folder (smoke, pilots, main run). The spend cap counts every
sibling runs/*/gateway.sqlite too (read at startup), so it caps the whole experiment.
Secrets: read from the process environment, else from ECONOCLM_SECRETS
(default ~/.econoclm/secrets.env): AGENT_PLATFORM_API_KEY, ECONOCONTEXT_BASE_URL.
"""

import argparse
import json
import logging
import os
import random
import re
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import prices
from .gateway_ledger import Ledger
from .meter import price_call_usd
from .usage import billed_output, to_usage

log = logging.getLogger("econoclm.gateway")

PATH = re.compile(r"^/run/(?P<run>[\w.:-]+)(?:/agent/(?P<agent>[\w.-]+))?/v1(?P<rest>/.*)$")
RETRY_STATUSES = {429, 503}
RETRY_BUDGET_S = 240.0
UPSTREAM_TIMEOUT_S = 300.0


def load_secrets(path: str | None = None) -> dict[str, str]:
    """KEY=VALUE lines from the secrets file; the process environment wins. Never printed."""
    out: dict[str, str] = {}
    file = Path(path or os.environ.get("ECONOCLM_SECRETS") or
                Path.home() / ".econoclm" / "secrets.env")
    if file.exists():
        for line in file.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"')
    for k in ("AGENT_PLATFORM_API_KEY", "ECONOCONTEXT_BASE_URL"):
        if os.environ.get(k):
            out[k] = os.environ[k]
    return out


class Config:
    """Everything the handler needs; set once in make_server()."""
    upstream: str = ""
    key: str = ""
    ledger: Ledger
    max_spend_usd: float = 40.0
    prior_spend_usd: float = 0.0   # spend in the other runs/*/gateway.sqlite (earlier phases)
    slots: threading.BoundedSemaphore
    retry_budget_s: float = RETRY_BUDGET_S
    backoff_base_s: float = 2.0
    backoff_cap_s: float = 30.0


class Gateway(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    cfg: Config  # set in make_server()

    def log_message(self, fmt, *args):  # quiet; calls are in the ledger
        pass

    # ------------------------------------------------------------ replies
    def reply_error(self, status: int, message: str) -> None:
        body = json.dumps({"error": {"message": f"econoclm gateway: {message}",
                                     "type": "econoclm_gateway", "code": status}}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.reply_error(404, "only POST /run/<run_id>/v1/chat/completions is served")

    # ------------------------------------------------------------ the route
    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        match = PATH.match(self.path)
        if not match or match["rest"] != "/chat/completions":
            return self.reply_error(404, f"unsupported path {self.path}")
        run_id = match["run"] + (f"/agent/{match['agent']}" if match["agent"] else "")
        cfg = self.cfg

        spent = cfg.ledger.total_spend() + cfg.prior_spend_usd
        if spent > cfg.max_spend_usd:
            return self.reply_error(429, f"SPEND CAP: total spend ${spent:.4f} exceeds "
                                         f"MAX_SPEND_USD=${cfg.max_spend_usd:.2f}; no new calls")

        try:
            body = json.loads(raw)
        except ValueError:
            return self.reply_error(400, "request body is not JSON")
        stream = bool(body.get("stream"))
        if stream and not (body.get("stream_options") or {}).get("include_usage"):
            # Measurement plumbing (both arms): ask for the usage chunk at the end.
            body["stream_options"] = {**(body.get("stream_options") or {}), "include_usage": True}
            raw = json.dumps(body).encode()

        t_queue = time.monotonic()
        with cfg.slots:
            queue_ms = (time.monotonic() - t_queue) * 1000
            status, payloads, attempts, wait_ms, latency_ms = self.forward(raw, stream)
        self.record(run_id, body, stream, status, payloads, attempts, wait_ms, latency_ms,
                    queue_ms)

    # ------------------------------------------------------------ upstream
    def open_upstream(self, raw: bytes):
        req = urllib.request.Request(
            self.cfg.upstream + "/chat/completions", raw,
            {"Content-Type": "application/json", "x-goog-api-key": self.cfg.key})
        try:
            return urllib.request.urlopen(req, timeout=UPSTREAM_TIMEOUT_S)
        except urllib.error.HTTPError as err:
            return err  # an HTTPError is also a readable reply

    def forward(self, raw: bytes, stream: bool):
        """Send upstream (retrying 429/503 with the same bytes), relay the final reply.
        Returns (status, JSON payloads, attempts, rate-limit wait ms, final latency ms)."""
        attempts, wait_ms = 0, 0.0
        started_all = time.monotonic()
        while True:
            attempts += 1
            t0 = time.monotonic()
            try:
                upstream = self.open_upstream(raw)
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                self.reply_error(502, f"upstream unreachable: {type(exc).__name__}")
                return 502, [], attempts, wait_ms, (time.monotonic() - t0) * 1000
            status = getattr(upstream, "status", None) or upstream.code
            elapsed = time.monotonic() - started_all
            if status in RETRY_STATUSES and elapsed < self.cfg.retry_budget_s:
                upstream.read()
                delay = min(self.cfg.backoff_cap_s, self.cfg.backoff_base_s * 2 ** (attempts - 1))
                delay = min(delay * random.uniform(0.5, 1.0),
                            max(0.0, self.cfg.retry_budget_s - elapsed))
                log.warning("upstream %s; retry %d in %.1fs", status, attempts, delay)
                time.sleep(delay)
                # The refused attempt and the backoff both count as rate-limit wait.
                wait_ms += (time.monotonic() - t0) * 1000
                continue
            payloads = self.relay(upstream, status, stream)
            return status, payloads, attempts, wait_ms, (time.monotonic() - t0) * 1000

    def relay(self, upstream, status: int, stream: bool) -> list[dict]:
        """Relay the reply unchanged (a stream line by line); return its JSON payloads."""
        self.send_response(status)
        self.send_header("Content-Type", upstream.headers.get("Content-Type", "application/json"))
        if not stream or status != 200:
            data = upstream.read()
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            try:
                return [json.loads(data)] if status == 200 and data else []
            except ValueError:
                return []
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
        return payloads

    # ------------------------------------------------------------ measure
    def record(self, run_id, body, stream, status, payloads, attempts, wait_ms, latency_ms,
               queue_ms) -> None:
        """Fail open: a measurement error is logged, never raised into the call."""
        try:
            usage = next((p["usage"] for p in reversed(payloads) if p.get("usage")), None)
            finish = None
            for p in payloads:
                for ch in p.get("choices") or []:
                    finish = ch.get("finish_reason") or finish
            u = to_usage(usage)
            _, inconsistent = billed_output(usage)
            anomaly = inconsistent or (status == 200 and u.output is None)
            self.cfg.ledger.insert(
                run_id,
                prompt_tokens=u.prompt_tokens, cached_tokens=u.cache_read,
                uncached_tokens=u.uncached_input, output_tokens=u.output,
                reasoning_tokens=u.reasoning,
                cost_usd=price_call_usd(u, prices.RATES) if usage else 0.0,
                latency_ms=round(latency_ms, 1), finish_reason=finish, http_status=status,
                upstream_attempts=attempts, ratelimit_wait_ms=round(wait_ms, 1),
                queue_ms=round(queue_ms, 1), model=body.get("model"), stream=int(stream),
                cached_reported=int(isinstance((usage or {}).get("prompt_tokens_details"), dict)),
                usage_anomaly=int(anomaly),
            )
            if anomaly:
                log.warning("usage anomaly in %s: %s", run_id, usage)
        except Exception:
            log.exception("could not record the call (the reply was still relayed)")


def prior_spend(ledger_path: str | Path) -> float:
    """Spend already recorded by the other phases' ledgers (sibling runs/*/gateway.sqlite),
    so MAX_SPEND_USD caps the whole experiment, not one phase."""
    me = Path(ledger_path).resolve()
    total = 0.0
    for other in me.parent.parent.glob("*/gateway.sqlite"):
        if other.resolve() != me:
            ledger = Ledger(other)
            try:
                total += ledger.total_spend()
            finally:
                ledger.close()
    return total


def make_server(ledger_path: str, port: int = 8787, upstream: str | None = None,
                key: str | None = None, max_spend_usd: float | None = None,
                max_inflight: int | None = None,
                prior_spend_usd: float | None = None) -> ThreadingHTTPServer:
    secrets = load_secrets()
    cfg = Config()
    cfg.upstream = (upstream or secrets.get("ECONOCONTEXT_BASE_URL") or "").rstrip("/")
    cfg.key = key if key is not None else secrets.get("AGENT_PLATFORM_API_KEY", "")
    if not cfg.upstream or not cfg.key:
        raise SystemExit("ECONOCONTEXT_BASE_URL and AGENT_PLATFORM_API_KEY must be set")
    cfg.ledger = Ledger(ledger_path)
    cfg.prior_spend_usd = (prior_spend(ledger_path) if prior_spend_usd is None
                           else float(prior_spend_usd))
    cfg.max_spend_usd = float(max_spend_usd if max_spend_usd is not None
                              else os.environ.get("MAX_SPEND_USD", "40"))
    cfg.slots = threading.BoundedSemaphore(
        int(max_inflight or os.environ.get("MAX_INFLIGHT", "4")))
    handler = type("BoundGateway", (Gateway,), {"cfg": cfg})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)  # localhost only
    server.daemon_threads = True
    return server


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ledger", required=True, help="path of gateway.sqlite")
    ap.add_argument("--port", type=int, default=8787)
    args = ap.parse_args()
    server = make_server(args.ledger, args.port)
    cfg = server.RequestHandlerClass.cfg
    print(f"econoclm gateway on http://127.0.0.1:{args.port} -> {cfg.upstream} "
          f"(spend cap ${cfg.max_spend_usd:.2f}, already spent in other phases "
          f"${cfg.prior_spend_usd:.4f}, max in flight {cfg.slots._initial_value})",
          flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
