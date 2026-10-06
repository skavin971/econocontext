"""Spike hook server (throwaway): proves Claude Code's hook mechanisms before we build on them.

Logs every hook event to events.jsonl. Two marked behaviours:
  PreToolUse  Bash command containing SPIKE_REWRITE -> updatedInput: echo REWRITTEN_BY_ECONO
  PostToolUse Bash output containing SPIKE_BIG      -> updatedToolOutput: a 3-line preview + note
Run: python spike_hooks.py [port]   (default 8790; listens on 0.0.0.0 so containers can reach it)
"""

import json
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

LOG = Path(__file__).with_name("events.jsonl")


def decide(event: dict) -> dict:
    name, tool = event.get("hook_event_name"), event.get("tool_name")
    tool_input = event.get("tool_input") or {}
    if name == "PreToolUse" and tool == "Bash" and "SPIKE_REWRITE" in tool_input.get("command", ""):
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
                                       "updatedInput": {**tool_input, "command": "echo REWRITTEN_BY_ECONO"}}}
    if name == "PostToolUse" and tool == "Bash" and "SPIKE_BIG" in json.dumps(event.get("tool_response")):
        preview = ("REPLACED_BY_ECONO preview\nSPIKE_BIG line 1\n"
                   "[399 more lines saved by EconoContext]")
        response = event.get("tool_response")
        # Session A showed a plain string is ignored; Bash's response is an object, so keep its shape.
        replaced = {**response, "stdout": preview} if isinstance(response, dict) else preview
        return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": replaced}}
    return {}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        try:
            event = json.loads(body or b"{}")
        except ValueError:
            event = {"unparsed": body.decode(errors="replace")[:500]}
        reply = decide(event)
        with LOG.open("a") as f:
            f.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "event": event,
                                "reply": reply}) + "\n")
        data = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8790
    print(f"spike hooks on 0.0.0.0:{port}, log {LOG}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
