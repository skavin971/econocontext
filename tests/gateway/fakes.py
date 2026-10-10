"""A fake model provider for the gateway tests: a local HTTP server that records every request and
answers from a function of the request body. Plus our gateway in front of it. Both listen on
ephemeral localhost ports; nothing leaves the machine."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

from gateway.server import Gateway, Provider, make_server

KEY = "test-provider-key-0123456789"
PINS = {"reasoning_effort": "medium", "return_token_ids": True}


def completion(message: dict, prompt_tokens: int = 100, completion_tokens: int = 10, extra: dict | None = None) -> bytes:
    return json.dumps({"id": "chatcmpl-test", "object": "chat.completion", "created": 0, "model": "qwen3.8:27b",
                       "choices": [{"index": 0, "finish_reason": "tool_calls" if message.get("tool_calls") else "stop",
                                    "message": message}],
                       "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                                 "total_tokens": prompt_tokens + completion_tokens}, **(extra or {})}).encode()


class FakeProvider:
    def __init__(self, reply):
        self.reply, self.requests = reply, []
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def answer(self, body):
                provider.requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
                status, raw = provider.reply(body)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):
                self.answer(json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0))))

            def do_GET(self):
                self.answer(None)

            def log_message(self, *args):
                pass
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"


def start(reply, log_dir, **limits) -> SimpleNamespace:
    """The fake provider and our gateway in front of it. Backoff sleeps are skipped."""
    upstream = FakeProvider(reply)
    gateway = Gateway(Provider("fake", upstream.base, "UNUSED", dict(PINS)), KEY, log_dir,
                      sleep=lambda seconds: None, **limits)
    server = make_server(gateway, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return SimpleNamespace(upstream=upstream, gateway=gateway, server=server, log_dir=log_dir,
                           url=f"http://127.0.0.1:{server.server_address[1]}",
                           stop=lambda: (server.shutdown(), upstream.server.shutdown()))


def log_rows(log_dir, run_id: str) -> list[dict]:
    return [json.loads(line) for line in (log_dir / run_id / "calls.jsonl").read_text().splitlines()]
