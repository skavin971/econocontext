"""Gate 2: one real chat completion through the gateway, then check its ledger row.

  python -m econoclm.bench.smoke --ledger runs/<date>/gateway.sqlite [--port 8787]

Sends a tiny request (max_tokens 256, room for Gemini's thinking and the answer) for
run id "smoke" with the arms' model, the way CLM would (a placeholder key; the gateway
adds the real one). Then prints
  - the model id the reply names, and its visible text,
  - which usage fields Vertex returned (prompt_tokens_details, reasoning tokens),
  - the ledger row (it never holds the key),
and checks: HTTP 200, every usage column filled, finish_reason "stop", non-empty
visible text, usage_anomaly 0, and cost_usd = the price of the row's tokens with
output_tokens > 0 (so the bill includes output and thinking). Exit 1 if any fails.
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

from ..core import prices
from ..core.gateway_ledger import Ledger
from .tblite.run import ARMS, ECONOCLM

USAGE_COLUMNS = ("prompt_tokens", "cached_tokens", "uncached_tokens", "output_tokens",
                 "reasoning_tokens", "cost_usd", "latency_ms", "finish_reason", "http_status")
MAX_TOKENS = 256


def arm_model() -> str:
    """The configs' litellm model, without litellm's provider prefix ('openai/')."""
    model = yaml.safe_load(ARMS["raw"].read_text())["model"]
    return model.split("/", 1)[1] if model.startswith("openai/") else model


def send(port: int, run_id: str, model: str) -> tuple[int, dict]:
    body = json.dumps({"model": model, "max_tokens": MAX_TOKENS,
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


def visible_text(reply: dict) -> str:
    choices = reply.get("choices") or [{}]
    return ((choices[0].get("message") or {}).get("content") or "").strip()


def priced(row: dict) -> float:
    """What the row's tokens cost at the price card."""
    return ((row["uncached_tokens"] or 0) * prices.PRICE_IN
            + (row["cached_tokens"] or 0) * prices.PRICE_CACHED
            + (row["output_tokens"] or 0) * prices.PRICE_OUT)


def check(status: int, reply: dict, row: dict | None) -> list[str]:
    problems = []
    if status != 200:
        problems.append(f"HTTP {status}: {reply.get('error')}")
    elif not visible_text(reply):
        problems.append("the reply has no visible text")
    if row is None:
        return problems + ["no ledger row"]
    problems += [f"ledger column {c} is empty" for c in USAGE_COLUMNS if row.get(c) is None]
    if row.get("finish_reason") not in (None, "stop"):
        problems.append(f"finish_reason is {row['finish_reason']!r}, not 'stop'")
    if row.get("usage_anomaly"):
        problems.append("usage_anomaly = 1 (the usage numbers don't add up)")
    if not (row.get("output_tokens") or 0) > 0:
        problems.append("output_tokens is not > 0")
    if not (row.get("cost_usd") or 0) > 0:
        problems.append("cost_usd is not > 0")
    elif abs(row["cost_usd"] - priced(row)) > 1e-12:
        problems.append(f"cost_usd {row['cost_usd']} != price of the row's tokens {priced(row)}")
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
    print(f"visible text:  {visible_text(reply)[:200]!r}")
    print(f"usage:         {json.dumps(usage)}")
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
