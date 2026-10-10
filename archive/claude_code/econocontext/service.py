"""EconoContext v2 hook service: Claude Code's HTTP hooks POST each event here; the reply is the
hook's JSON (see decide.py). One process serves every run; each session gets its own database.

  POST /runs   {"run", "predictor": "jev"|"prior", "force": [...], "sessions_dir",
                "path_map": {container_prefix: host_prefix}}      register a run before it starts
  POST /hook   (header X-Econo-Run: <run>)                           one hook event -> reply JSON
  GET  /flag?run=<run>                                               a pending compaction, or null

Run: python -m econocontext.service [--port 8790] [--config config/v2.yaml]
"""

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml

from . import decide, transcript
from .predictor.jev import Jev
from .predictor.prior import Prior
from .pricing.lifecycle import from_card
from .session import Session

ROOT = Path(__file__).resolve().parents[1]


class Service:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.prices = from_card(cfg["price"]["provider"], cfg["price"]["model"],
                                cfg["price"]["hit_share"], cfg["price"]["expected_output"])
        self.runs: dict[str, dict] = {}
        self.sessions: dict[tuple[str, str], tuple[Session, threading.Lock]] = {}
        self.lock = threading.Lock()

    def register(self, spec: dict) -> dict:
        run = spec["run"]
        spec = {"predictor": "jev", "force": [], "path_map": {}, **spec}
        Path(spec["sessions_dir"]).mkdir(parents=True, exist_ok=True)
        (Path(spec["sessions_dir"]) / "run.json").write_text(json.dumps(spec, indent=2))
        self.runs[run] = spec
        return {"ok": True, "run": run}

    def session(self, run: str, session_id: str) -> tuple[Session, threading.Lock]:
        with self.lock:
            key = (run, session_id)
            if key not in self.sessions:
                path = Path(self.runs[run]["sessions_dir"]) / f"{session_id}.sqlite3"
                self.sessions[key] = (Session(path), threading.Lock())
            return self.sessions[key]

    def host_path(self, run: str, path: str | None) -> str | None:
        for prefix, host in self.runs[run]["path_map"].items():
            if path and path.startswith(prefix):
                return host + path[len(prefix):]
        return path

    def predictor(self, run: str):
        if self.runs[run]["predictor"] == "prior":
            return Prior()
        return Jev(self.cfg["jev"]["model"], self.cfg["jev"]["timeout_seconds"], self.cfg["jev"]["max_input_tokens"])

    def hook(self, run: str, ev: dict) -> dict:
        if run not in self.runs or not ev.get("session_id"):
            return {}
        session, lock = self.session(run, ev["session_id"])
        name = ev.get("hook_event_name")
        with lock:
            path = self.host_path(run, ev.get("transcript_path"))
            if name == "UserPromptSubmit" and not session.get("task"):
                session.set("task", ev.get("prompt"))
            if name == "SubagentStop" and ev.get("agent_id"):
                session.set("idle_workers", {**session.get("idle_workers", {}),
                                             ev["agent_id"]: ev.get("agent_type")})
            if name == "SubagentStart" and ev.get("agent_id"):
                idle = session.get("idle_workers", {})
                idle.pop(ev["agent_id"], None)
                session.set("idle_workers", idle)
            if name == "PostCompact":
                decide.post_compact(session, ev)
            if name not in ("PreToolUse", "PostToolUse"):
                return {}
            usages = transcript.calls(path) if path else []
            session.set("calls", len(usages))
            ctx = decide.Ctx(session, self.cfg, self.prices, self.predictor(run),
                             set(self.runs[run]["force"]), len(usages),
                             transcript.prompt_tokens(usages[-1]) if usages else self.cfg["session"]["fixed_prefix"],
                             session.get("task") or "",
                             lambda: transcript.conversation(path, self.cfg["jev"]["max_state_chars"]) if path else [])
            if name == "PreToolUse":
                return decide.pre_tool(ctx, ev)
            reply = decide.post_tool(ctx, ev)
            if not ev.get("agent_id"):  # the main agent: maybe flag a compaction for the driver
                decide.live_check(ctx)
            return reply

    def flag(self, run: str) -> dict:
        for (r, session_id), (session, _) in list(self.sessions.items()):
            if r == run and session.get("compact"):
                return {"session_id": session_id, "compact": session.get("compact")}
        return {"compact": None}


def make_handler(service: Service):
    class Handler(BaseHTTPRequestHandler):
        def reply(self, data: dict, status: int = 200) -> None:
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            try:
                data = json.loads(raw or b"{}")
            except ValueError:
                return self.reply({"error": "invalid JSON"}, 400)
            path = urlparse(self.path).path
            try:
                if path == "/runs":
                    return self.reply(service.register(data))
                if path == "/hook":
                    return self.reply(service.hook(self.headers.get("X-Econo-Run", ""), data))
            except Exception as exc:  # fail open: a broken rule must never break the agent
                print(f"error on {path}: {type(exc).__name__}: {str(exc)[:300]}", flush=True)
                return self.reply({} if path == "/hook" else {"error": str(exc)[:300]},
                                  200 if path == "/hook" else 500)
            self.reply({"error": f"unknown path {path}"}, 404)

        def do_GET(self):
            url = urlparse(self.path)
            if url.path == "/flag":
                return self.reply(service.flag(parse_qs(url.query).get("run", [""])[0]))
            if url.path == "/health":
                return self.reply({"ok": True, "runs": list(service.runs)})
            self.reply({"error": "not found"}, 404)

        def log_message(self, *args):
            pass
    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--config", default=str(ROOT / "config" / "v2.yaml"))
    args = parser.parse_args()
    service = Service(yaml.safe_load(Path(args.config).read_text()))
    # 0.0.0.0: task containers reach it as host.docker.internal (spike 1a, check a).
    print(f"econocontext v2 hook service on 0.0.0.0:{args.port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", args.port), make_handler(service)).serve_forever()


if __name__ == "__main__":
    main()
