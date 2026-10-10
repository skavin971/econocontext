"""Does a model admit realistic agent requests often enough to run the study?

Sends one recorded, real SWE-agent turn (12 messages, ~14 KB) to each model N
times, spaced out, retrying exactly as the harness does (8 retries, backoff
doubling to 60 s). The rule, fixed before probing: usable if at least 7 of 10
requests are admitted within that budget. Every admitted call is priced and
written to the results, so the probe's own cost is on the record.

    python research/live_study/probe.py --models gemini-3.6-flash
"""

import argparse
import asyncio
import json
import time
from datetime import date
from pathlib import Path

from models import MODELS, config

from econocontext.config import load_env_file
from econocontext.runtime.agent_loop import backoff, retryable
from econocontext.runtime.backend import build_backend
from econocontext.runtime.telemetry import charge, normalize

ROOT = Path(__file__).resolve().parents[2]
# A real turn from smoke-policies-3 (pyflakes-325), refused repeatedly by Gemini 3.5 Flash.
REQUEST = (
    "data/live-study/artifacts/1740d01276e25ed3d5ba3865eaa9e745e1960f5025e30d66e19609a037708559"
)
RETRIES = 8
USABLE = 0.7


async def probe(name, count, spacing):
    cfg = config(name, ROOT / "data" / "live-study", None)
    backend = build_backend(cfg)
    body = json.loads((ROOT / REQUEST).read_text())["body"]
    body = dict(body, model=cfg.model)
    trials = []
    for n in range(count):
        started, refusals, outcome = time.monotonic(), 0, None
        for retry in range(RETRIES + 1):
            try:
                response = await backend.complete(body, {})
            except Exception as exc:
                if not retryable(exc) or retry == RETRIES:
                    outcome = dict(admitted=False, error=f"{type(exc).__name__}: {exc}"[:200])
                    break
                refusals += 1
                await asyncio.sleep(backoff(exc, retry))
                continue
            usage = normalize(response.usage)
            outcome = dict(admitted=True, usage=usage, cost=charge(usage, cfg.pricing))
            break
        outcome.update(trial=n, refusals=refusals, seconds=round(time.monotonic() - started, 1))
        trials.append(outcome)
        print(
            f"  {name} #{n}: admitted={outcome['admitted']} refusals={refusals} "
            f"{outcome['seconds']}s",
            flush=True,
        )
        if n < count - 1:
            await asyncio.sleep(spacing)
    admitted = sum(t["admitted"] for t in trials)
    return dict(
        model=name,
        pricing=cfg.pricing.revision,
        trials=trials,
        admitted=admitted,
        of=count,
        usable=admitted >= USABLE * count,
        refusals=sum(t["refusals"] for t in trials),
        cost=sum(t.get("cost") or 0 for t in trials),
    )


async def main(args):
    out = ROOT / "docs" / "runs" / f"{date.today().isoformat()}-live-study" / "probe"
    out.mkdir(parents=True, exist_ok=True)
    results = [await probe(m, args.count, args.spacing) for m in args.models]
    path = out / "results.json"
    previous = json.loads(path.read_text()) if path.exists() else []
    path.write_text(json.dumps(previous + [dict(at=int(time.time()), results=results)], indent=1))
    for r in results:
        print(
            f"{r['model']}: {r['admitted']}/{r['of']} admitted, {r['refusals']} refusals, "
            f"${r['cost']:.4f}  -> {'USABLE' if r['usable'] else 'NOT USABLE'}"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="+", choices=sorted(MODELS), required=True)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--spacing", type=float, default=20)
    load_env_file(ROOT / ".env")
    asyncio.run(main(parser.parse_args()))
