"""One small OpenAI-compatible proxy between our agents and a model provider (localhost only).

Why it exists: every agent, ours and CLM's, reaches models only through here. So one place:
- holds the provider key (agents send a placeholder);
- paces upstream requests to the provider's limit (Purdue: 20 a minute, retries included);
- enforces call caps;
- pins the request fields the measurement depends on;
- logs every call (the exact body sent upstream, the exact body returned) to
  <log-dir>/<run_id>/calls.jsonl, the only input measure/ needs.

Routes (run ids: letters, digits and . _ + -, starting with a letter or digit):
  POST /run/<run_id>/v1/chat/completions   non-streaming only; header X-Econo-Call-Kind (default agent)
  GET  /run/<run_id>/v1/models              the provider's model list
  GET  /health                              provider, log dir, caps and today's request count

Before a chat request goes upstream (docs/tier-a-decisions.md):
- earlier reasoning is removed from every assistant message (`reasoning`, `reasoning_content`, and
  the whole `provider_specific_fields`, which echoes it), for every agent and every provider;
- the provider's pins are set: Purdue gets reasoning_effort=medium and return_token_ids=true.

Upstream failures (a JSON null or empty body, 429, 5xx, a timeout) are retried with backoff, 6
tries at most. A cap refusal is an HTTP 429 whose message contains "reached"; it is logged with
call_no null. The per-run and daily counts are rebuilt from the log at startup; days are UTC.

Run: .venv/bin/python -m gateway.server --provider purdue [--port 8787] [--log-dir runs/gateway]
"""

import argparse
import copy
import http.client
import json
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
RUN = r"[A-Za-z0-9][A-Za-z0-9._+-]*"
CHAT, MODELS = re.compile(rf"^/run/({RUN})/v1/chat/completions$"), re.compile(rf"^/run/({RUN})/v1/models$")
KIND = re.compile(r"^[a-z_]{1,32}$")


@dataclass
class Provider:
    name: str
    base: str                       # the provider's API root
    key_env: str                    # the .env variable holding its key
    pins: dict = field(default_factory=dict)


PROVIDERS = {"purdue": Provider("purdue", "https://genai.rcac.purdue.edu/api", "GENAI_API_KEY",
                                {"reasoning_effort": "medium", "return_token_ids": True})}
# TODO(vertex): the Gemini route of the archived Omnigent gateway (archive/omnigent/) is not ported yet.


def read_env(name: str, env: Path = ROOT / ".env") -> str:
    for line in env.read_text().splitlines():
        if line.startswith(f"{name}="):
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            if value:
                return value
    raise SystemExit(f"{name} is not set in .env")


class Limiter:
    """At most `limit` upstream requests in any rolling window; callers wait, never refused."""

    def __init__(self, limit: int, window_s: float = 60.0, clock=time.monotonic, sleep=time.sleep):
        self.limit, self.window, self.clock, self.sleep = limit, window_s, clock, sleep
        self.times, self.lock = deque(), threading.Lock()

    def acquire(self) -> float:
        waited = 0.0
        with self.lock:  # held while waiting: the waiters queue up behind it
            while True:
                now = self.clock()
                while self.times and now - self.times[0] >= self.window:
                    self.times.popleft()
                if len(self.times) < self.limit:
                    self.times.append(now)
                    return waited
                pause = self.window - (now - self.times[0])
                self.sleep(pause)
                waited += pause


class Gateway:
    def __init__(self, provider: Provider, key: str, log_dir: Path, max_calls_per_run: int = 150,
                 max_requests_per_day: int = 8000, rpm: int = 20, window_s: float = 60.0, tries: int = 6,
                 backoff_s: float = 2.0, timeout_s: float = 600.0, sleep=time.sleep):
        self.provider, self.key, self.log_dir = provider, key, Path(log_dir)
        self.max_run, self.max_day, self.tries, self.backoff, self.timeout = \
            max_calls_per_run, max_requests_per_day, tries, backoff_s, timeout_s
        self.limiter, self.sleep, self.lock = Limiter(rpm, window_s, sleep=sleep), sleep, threading.Lock()
        self.calls: dict[str, int] = {}
        self.day, self.day_count = self.today(), self.logged_today()

    @staticmethod
    def today() -> str:
        return datetime.now(timezone.utc).date().isoformat()

    def logged(self, run_id: str) -> list[dict]:
        path = self.log_dir / run_id / "calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def logged_today(self) -> int:
        return sum(1 for path in self.log_dir.glob("*/calls.jsonl") for line in path.read_text().splitlines()
                   if (row := json.loads(line)).get("call_no") and row["t_start"].startswith(self.today()))

    def health(self) -> dict:
        with self.lock:
            if self.day != self.today():
                self.day, self.day_count = self.today(), 0
            return {"ok": True, "provider": self.provider.name, "log_dir": str(self.log_dir), "day": self.day,
                    "requests_today": self.day_count, "max_requests_per_day": self.max_day,
                    "max_calls_per_run": self.max_run}

    def prepare(self, body: dict) -> tuple[dict, list[str]]:
        """The body sent upstream: earlier reasoning removed, the provider's pins set."""
        sent, edits, stripped = copy.deepcopy(body), [], 0
        for message in sent.get("messages") or []:
            if message.get("role") != "assistant":
                continue
            stripped += any([message.pop(f, None) is not None
                             for f in ("reasoning", "reasoning_content", "provider_specific_fields")])
        if stripped:
            edits.append(f"removed earlier reasoning from {stripped} assistant message(s)")
        for name, value in self.provider.pins.items():
            if sent.get(name) != value:
                edits.append(f"set {name}={value!r}" + (f" (client sent {sent[name]!r})" if name in sent else ""))
            sent[name] = value
            kwargs = sent.get("chat_template_kwargs")
            if isinstance(kwargs, dict) and name in kwargs and kwargs[name] != value:
                edits.append(f"set chat_template_kwargs.{name}={value!r} (client sent {kwargs[name]!r})")
                kwargs[name] = value
        return sent, edits

    def upstream(self, method: str, path: str, body: dict | None = None) -> tuple[int | None, bytes]:
        data = json.dumps(body).encode() if body is not None else None
        request = Request(self.provider.base + path, data=data, method=method,
                          headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return response.status, response.read()
        except HTTPError as exc:
            return exc.code, exc.read()
        except (URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
            return None, f"{type(exc).__name__}: {exc}".encode()

    def chat(self, run_id: str, kind: str, body: dict) -> tuple[int, bytes]:
        started, t_start = time.monotonic(), datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        with self.lock:
            if self.day != self.today():
                self.day, self.day_count = self.today(), 0
            done = self.calls.setdefault(run_id, sum(1 for row in self.logged(run_id) if row.get("call_no")))
            refused = (f"per-run call cap reached ({self.max_run} calls)" if done >= self.max_run else
                       f"daily request cap reached ({self.max_day} requests)" if self.day_count >= self.max_day else None)
            if not refused:
                self.calls[run_id], self.day_count = done + 1, self.day_count + 1
            call_no = None if refused else done + 1
        if refused:
            error = {"error": {"message": f"gateway: {refused}", "type": "cap"}}
            self.log(run_id, {"call_no": None, "kind": kind, "t_start": t_start, "latency_ms": 0, "limiter_wait_ms": 0,
                              "status": 429, "retries": 0, "edits": [], "refused": refused, "request": body,
                              "response": error, "usage": None})
            return 429, json.dumps(error).encode()
        sent, edits = self.prepare(body)
        waited, retries, status, raw, parsed = 0.0, 0, None, b"", None
        for attempt in range(self.tries):
            waited += self.limiter.acquire()
            status, raw = self.upstream("POST", "/chat/completions", sent)
            try:
                parsed = json.loads(raw) if raw.strip() else None
            except ValueError:
                parsed = None
            if status is not None and status != 429 and status < 500 and (status != 200 or isinstance(parsed, dict)):
                break
            if attempt + 1 < self.tries:
                self.sleep(self.backoff * 2 ** attempt)
                retries += 1
        if status != 200 or not isinstance(parsed, dict):
            if status is None or (status == 200 and not isinstance(parsed, dict)):
                message = f"gateway: upstream failed after {retries + 1} tries ({raw[:200].decode(errors='replace')})"
                status, raw, parsed = 502, json.dumps({"error": {"message": message, "type": "upstream"}}).encode(), None
        self.log(run_id, {"call_no": call_no, "kind": kind, "t_start": t_start,
                          "latency_ms": round((time.monotonic() - started) * 1000),
                          "limiter_wait_ms": round(waited * 1000), "status": status, "retries": retries, "edits": edits,
                          "request": sent, "response": parsed if parsed is not None else raw.decode(errors="replace"),
                          "usage": parsed.get("usage") if isinstance(parsed, dict) else None})
        return status, raw

    def log(self, run_id: str, row: dict) -> None:
        line = json.dumps({"run_id": run_id, "provider": self.provider.name,
                           "model": (row.get("request") or {}).get("model"), **row})
        with self.lock:
            path = self.log_dir / run_id / "calls.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a") as f:
                f.write(line.replace(self.key, "[REDACTED]") + "\n")


def make_server(gateway: Gateway, port: int = 8787) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def reply(self, status: int, raw: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def error(self, status: int, message: str) -> None:
            self.reply(status, json.dumps({"error": {"message": f"gateway: {message}", "type": "gateway"}}).encode())

        def do_POST(self):
            match = CHAT.match(self.path)
            if not match:
                return self.error(404, f"unknown path {self.path}")
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
            except ValueError:
                return self.error(400, "the body is not JSON")
            if not isinstance(body, dict):
                return self.error(400, "the body is not a JSON object")
            if body.get("stream"):
                return self.error(400, "streaming not supported")
            kind = self.headers.get("X-Econo-Call-Kind") or "agent"
            self.reply(*gateway.chat(match.group(1), kind if KIND.match(kind) else "agent", body))

        def do_GET(self):
            if self.path == "/health":
                return self.reply(200, json.dumps(gateway.health()).encode())
            if not MODELS.match(self.path):
                return self.error(404, f"unknown path {self.path}")
            gateway.limiter.acquire()
            status, raw = gateway.upstream("GET", "/models")
            self.reply(status or 502, raw)

        def log_message(self, *args):
            pass
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--provider", required=True, choices=["purdue", "vertex"])
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--log-dir", default=str(ROOT / "runs" / "gateway"))
    p.add_argument("--max-calls-per-run", type=int, default=150)
    p.add_argument("--max-requests-per-day", type=int, default=8000)
    p.add_argument("--rpm", type=int, default=20, help="upstream requests per rolling 60 s (Purdue: 20)")
    a = p.parse_args()
    if a.provider == "vertex":
        raise SystemExit("--provider vertex is a TODO: only purdue is implemented")
    provider = PROVIDERS[a.provider]
    gateway = Gateway(provider, read_env(provider.key_env), Path(a.log_dir), a.max_calls_per_run,
                      a.max_requests_per_day, a.rpm)
    print(f"gateway: {provider.name} on 127.0.0.1:{a.port}, log {a.log_dir}, {a.rpm}/min, "
          f"{a.max_calls_per_run} calls per run, {a.max_requests_per_day} requests per day", flush=True)
    make_server(gateway, a.port).serve_forever()


if __name__ == "__main__":
    main()
