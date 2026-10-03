"""Gate 2: one real chat completion through the gateway, then check its ledger row.

  python -m econoclm.bench.smoke --ledger runs/<date>/gateway.sqlite [--port 8787]

Sends a tiny request (max_tokens 16) for run id "smoke" with the arms' model, the
way CLM would (a placeholder key; the gateway adds the real one). Then prints
  - the model id the reply names,
  - which usage fields Vertex returned (prompt_tokens_details, reasoning tokens),
  - the ledger row (it never holds the key),
and checks: HTTP 200, every usage column filled, cost_usd > 0. Exit 1 if any fails.
Costs well under one cent.
"""

import argparse
import datetime as dt
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

from ..core.gateway_ledger import Ledger
from .tblite.run import ARMS, ECONOCLM

USAGE_COLUMNS = ("prompt_tokens", "cached_tokens", "uncached_tokens", "output_tokens",
                 "reasoning_tokens", "cost_usd", "latency_ms", "finish_reason", "http_status")


def arm_model() -> str:
    """The configs' litellm model, without litellm's provider prefix ('openai/')."""
    model = yaml.safe_load(ARMS["raw"].read_text())["model"]
    return model.split("/", 1)[1] if model.startswith("openai/") else model


def send(port: int, run_id: str, model: str) -> tuple[int, dict]:
    body = json.dumps({"model": model, "max_tokens": 16,
                       "messages": [{"role": "user", "content": "Reply with the word OK."}]}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/run/{run_id}/v1/chat/completions", body,
                                 {"Content-Type": "application/json",
                                  "Authorization": "Bearer placeholder"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as err:
        return err.code, {"error": err.read()[:500].decode(errors="replace")}


def ledger_row(ledger_path: Path, run_id: str, wait_s: float = 3.0) -> dict | None:
    deadline = time.monotonic() + wait_s
    ledger = Ledger(ledger_path)
    try:
        while True:
            row = ledger.latest(run_id)
            if row or time.monotonic() > deadline:
                return row
            time.sleep(0.05)
    finally:
        ledger.close()


def check(status: int, reply: dict, row: dict | None) -> list[str]:
    problems = []
    if status != 200:
        problems.append(f"HTTP {status}: {reply.get('error')}")
    if row is None:
        return problems + ["no ledger row"]
    problems += [f"ledger column {c} is empty" for c in USAGE_COLUMNS if row.get(c) is None]
    if not (row.get("cost_usd") or 0) > 0:
        problems.append("cost_usd is not > 0")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ledger", type=Path,
                    default=ECONOCLM / "runs" / dt.date.today().isoformat() / "gateway.sqlite")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--run-id", default="smoke")
    ap.add_argument("--model", default=None, help="default: the model in the arm configs")
    args = ap.parse_args(argv)

    model = args.model or arm_model()
    status, reply = send(args.port, args.run_id, model)
    usage = reply.get("usage") or {}
    print(f"request model: {model}")
    print(f"reply model:   {reply.get('model')}")
    print(f"HTTP status:   {status}")
    print(f"usage fields:  {sorted(usage)}")
    print(f"  prompt_tokens_details present: {isinstance(usage.get('prompt_tokens_details'), dict)}"
          f" -> {usage.get('prompt_tokens_details')}")
    print(f"  completion_tokens_details:     {usage.get('completion_tokens_details')}")
    row = ledger_row(args.ledger, args.run_id)
    print("ledger row:    " + json.dumps(row, indent=2, default=str))
    problems = check(status, reply, row)
    for p in problems:
        print(f"FAIL: {p}")
    print("GATE 2: PASS" if not problems else "GATE 2: FAIL")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
