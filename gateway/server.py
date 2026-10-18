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
- the provider's pins are set, for every agent. Purdue gets CLM's TBLite sampling and output cap
  (temperature 0.7, top_p 0.95, max_tokens 2,048, chat_template_kwargs.enable_thinking=true; step 10b),
  reasoning_effort=medium and return_token_ids=true. Other chat_template_kwargs are kept; a client's
  max_completion_tokens is removed, since vLLM would use it instead of max_tokens. Every change is
  listed in the log line's `edits`.
- the output cap can differ per benchmark (step 13a): --max-tokens-for PREFIX=N pins max_tokens N on runs whose
  id starts with PREFIX (BrowseComp-Plus: 4,096); every other run keeps --max-tokens (TBLite: 2,048).
- a benchmark's logs can live elsewhere (step 13a): --log-dir-for PREFIX=DIR logs runs whose id starts with
  PREFIX under DIR (BrowseComp-Plus: outside the repo, since its text must not be published). The per-run and
  daily counts read every log directory.
- a judge call (X-Econo-Call-Kind: judge) is sent as the client wrote it: the pins are for the agents being
  measured, and a judge keeps its own model and sampling (step 13a). It is logged like any call, under its
  own run id.

Upstream failures (a JSON null or empty body, 429, 5xx, a timeout) are retried with backoff, 6
tries at most. A cap refusal is an HTTP 429 whose message contains "reached"; it is logged with
call_no null. The per-run and daily counts are rebuilt from the log at startup; days are UTC.

Run: .venv/bin/python -m gateway.server --provider purdue [--port 8787] [--log-dir runs/gateway] [--max-tokens 2048]
     [--max-tokens-for bcp=4096] [--log-dir-for bcp=~/econo/bcp/gateway]
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
    pins: dict = field(default_factory=dict)        # request fields set on every chat request
    template: dict = field(default_factory=dict)    # chat_template_kwargs entries, set the same way


# CLM's TBLite setting for every agent (step 10b): its sampling, thinking on, and up to 2,048 tokens out.
SAMPLING = {"temperature": 0.7, "top_p": 0.95, "max_tokens": 2048}
PROVIDERS = {"purdue": Provider("purdue", "https://genai.rcac.purdue.edu/api", "GENAI_API_KEY",
                                {"reasoning_effort": "medium", "return_token_ids": True, **SAMPLING},
                                {"enable_thinking": True}),
             # Tier B ("PFLOP accuracy", pflop_accuracy/README.md): our own vLLM serving the same model, with the same
             # pins as Purdue, so the requests and logs are identical; its usage also reports the cached tokens.
             "vllm": Provider("vllm", "http://127.0.0.1:8000/v1", "VLLM_API_KEY",
                              {"reasoning_effort": "medium", "return_token_ids": True, **SAMPLING},
                              {"enable_thinking": True})}
OPTIONAL_KEY = {"vllm"}       # a local vLLM needs no key; a dummy one is sent
# TODO(vertex): the Gemini route of the Omnigent gateway (omnigent_layer/ at tag econo-jev-final) is not ported yet.


def read_env(name: str, env: Path = ROOT / ".env", default: str | None = None) -> str:
    for line in (env.read_text().splitlines() if env.exists() else []):
        if line.startswith(f"{name}="):
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            if value:
                return value
    if default is not None:
        return default
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
                 backoff_s: float = 2.0, timeout_s: float = 600.0, sleep=time.sleep,
                 max_tokens_for: dict[str, int] | None = None, log_dir_for: dict[str, Path] | None = None):
        self.provider, self.key, self.log_dir = provider, key, Path(log_dir)
        self.log_dir_for = {prefix: Path(d).expanduser() for prefix, d in (log_dir_for or {}).items()}
        self.max_tokens_for = dict(max_tokens_for or {})     # run-id prefix -> output cap
        self.max_run, self.max_day, self.tries, self.backoff, self.timeout = \
            max_calls_per_run, max_requests_per_day, tries, backoff_s, timeout_s
        self.limiter, self.sleep, self.lock = Limiter(rpm, window_s, sleep=sleep), sleep, threading.Lock()
        self.calls: dict[str, int] = {}
        self.day, self.day_count = self.today(), self.logged_today()

    @staticmethod
    def today() -> str:
        return datetime.now(timezone.utc).date().isoformat()

    def logged(self, run_id: str) -> list[dict]:
        path = self.dir_for(run_id) / run_id / "calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def logged_today(self) -> int:
        return sum(1 for d in {self.log_dir, *self.log_dir_for.values()} for path in d.glob("*/calls.jsonl")
                   for line in path.read_text().splitlines()
                   if (row := json.loads(line)).get("call_no") and row["t_start"].startswith(self.today()))

    def health(self) -> dict:
        with self.lock:
            if self.day != self.today():
                self.day, self.day_count = self.today(), 0
            return {"ok": True, "provider": self.provider.name, "upstream": self.provider.base,
                    "log_dir": str(self.log_dir), "day": self.day,
                    "requests_today": self.day_count, "max_requests_per_day": self.max_day,
                    "max_calls_per_run": self.max_run, "max_tokens": self.provider.pins.get("max_tokens"),
                    "max_tokens_for": self.max_tokens_for,
                    "log_dir_for": {k: str(v) for k, v in self.log_dir_for.items()}}

    def dir_for(self, run_id: str) -> Path:
        """Where a run's calls are logged: its benchmark's directory, if one is set, else the log dir."""
        return next((d for prefix, d in self.log_dir_for.items() if run_id.startswith(prefix)), self.log_dir)

    def pins_for(self, run_id: str | None) -> dict:
        """The provider's pins, with the output cap of the run's benchmark (its run-id prefix), if one is set."""
        pins = dict(self.provider.pins)
        for prefix, cap in self.max_tokens_for.items():
            if run_id and run_id.startswith(prefix) and "max_tokens" in pins:
                pins["max_tokens"] = cap
        return pins

    def prepare(self, body: dict, run_id: str | None = None, kind: str = "agent") -> tuple[dict, list[str]]:
        """The body sent upstream: earlier reasoning removed, the provider's pins set (a judge call: as sent)."""
        if kind == "judge":
            return copy.deepcopy(body), []
        pins = self.pins_for(run_id)
        sent, edits, stripped = copy.deepcopy(body), [], 0
        for message in sent.get("messages") or []:
            if message.get("role") != "assistant":
                continue
            stripped += any([message.pop(f, None) is not None
                             for f in ("reasoning", "reasoning_content", "provider_specific_fields")])
        if stripped:
            edits.append(f"removed earlier reasoning from {stripped} assistant message(s)")
        for name, value in pins.items():
            if sent.get(name) != value:
                edits.append(f"set {name}={value!r}" + (f" (client sent {sent[name]!r})" if name in sent else ""))
            sent[name] = value
            kwargs = sent.get("chat_template_kwargs")
            if isinstance(kwargs, dict) and name in kwargs and kwargs[name] != value:
                edits.append(f"set chat_template_kwargs.{name}={value!r} (client sent {kwargs[name]!r})")
                kwargs[name] = value
        if "max_tokens" in pins and "max_completion_tokens" in sent:
            edits.append(f"removed max_completion_tokens (client sent {sent.pop('max_completion_tokens')!r})")
        if self.provider.template:
            kwargs = sent.get("chat_template_kwargs")
            kwargs = sent["chat_template_kwargs"] = kwargs if isinstance(kwargs, dict) else {}
            for name, value in self.provider.template.items():
                if kwargs.get(name) != value:
                    edits.append(f"set chat_template_kwargs.{name}={value!r}"
                                 + (f" (client sent {kwargs[name]!r})" if name in kwargs else ""))
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
        sent, edits = self.prepare(body, run_id, kind)
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
            path = self.dir_for(run_id) / run_id / "calls.jsonl"
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
    p.add_argument("--provider", required=True, choices=["purdue", "vllm", "vertex"])
    p.add_argument("--upstream", help="the provider's API root (vllm: http://<host>:8000/v1, the default)")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--log-dir", default=str(ROOT / "runs" / "gateway"))
    p.add_argument("--max-calls-per-run", type=int, default=150)
    p.add_argument("--max-requests-per-day", type=int, default=8000)
    p.add_argument("--rpm", type=int, default=20, help="upstream requests per rolling 60 s (Purdue: 20)")
    p.add_argument("--max-tokens", type=int, default=SAMPLING["max_tokens"],
                   help="the output cap pinned on every request (CLM's TBLite setting: 2,048)")
    p.add_argument("--log-dir-for", action="append", default=[], metavar="PREFIX=DIR",
                   help="log runs whose id starts with PREFIX under DIR (BrowseComp-Plus: outside the repo)")
    p.add_argument("--max-tokens-for", action="append", default=[], metavar="PREFIX=N",
                   help="a benchmark's own output cap, for run ids starting with PREFIX (BrowseComp-Plus: bcp=4096)")
    a = p.parse_args()
    if a.provider == "vertex":
        raise SystemExit("--provider vertex is a TODO: only purdue is implemented")
    provider = PROVIDERS[a.provider]
    provider = Provider(provider.name, (a.upstream or provider.base).rstrip("/"), provider.key_env,
                        {**provider.pins, "max_tokens": a.max_tokens}, provider.template)
    caps = {prefix: int(n) for prefix, _, n in (x.partition("=") for x in a.max_tokens_for)}
    dirs = {prefix: Path(d) for prefix, _, d in (x.partition("=") for x in a.log_dir_for)}
    key = read_env(provider.key_env, default="EMPTY" if provider.name in OPTIONAL_KEY else None)
    gateway = Gateway(provider, key, Path(a.log_dir), a.max_calls_per_run,
                      a.max_requests_per_day, a.rpm, max_tokens_for=caps, log_dir_for=dirs)
    print(f"gateway: {provider.name} ({provider.base}) on 127.0.0.1:{a.port}, log {a.log_dir}, {a.rpm}/min, "
          f"{a.max_calls_per_run} calls per run, {a.max_requests_per_day} requests per day; output cap by run-id prefix "
          f"{caps or 'none'}; log dirs by run-id prefix {dirs or 'none'}; pins "
          f"{json.dumps({**provider.pins, 'chat_template_kwargs': provider.template})}", flush=True)
    make_server(gateway, a.port).serve_forever()


if __name__ == "__main__":
    main()
