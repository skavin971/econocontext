"""One small coding task on a stock harness (Claude Code or Gemini CLI), end to end.

Why it exists: it proves a harness works before anything is measured at scale:
Omnigent starts it, it uses its own tools, every model call passes the gateway
(placeholder key in, real key upstream), and the reply comes back. It makes a handful
of model calls on purpose: capped by calls, and for Claude Code by dollars too.

  1. make a tiny git repository with one bug, in data/work/<run>
  2. register the run (host omnigent:<harness>) and fill harness/specs/<harness>/
  3. one Omnigent session, one task; print the reply, the diff and the measured calls

Needs: the Omnigent server (omnigent start) and the gateway
(python -m omnigent_layer.gateway) running, and the harness installed (README).

Run: .venv/bin/python harness/smoke.py [--harness claude-code|gemini-omnigent] [--arm econo]
"""

import argparse
import asyncio
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness.session import run_session, write_spec  # noqa: E402

from econocontext.pricing.ledger import summary  # noqa: E402
from omnigent_layer import HOME, engine_for, register_run  # noqa: E402

CALC = '''def add(a, b):
    """Return the sum of a and b."""
    return a - b
'''
TASK = ("calc.py's add() returns the wrong result. Fix it with the smallest change, "
        "then reply with one sentence saying what you changed.")
# Per harness: its host name, and the per-run overrides (price card, caps).
HARNESSES = {
    "claude-code": ("omnigent:claude-code",
                    {"model": {"provider": "anthropic", "name": "claude-sonnet-5"},
                     "limits": {"max_model_calls": 15, "per_instance_budget_usd": 0.30}}),
    "gemini-omnigent": ("omnigent:gemini", {"limits": {"max_model_calls": 10}}),
}


def make_repo(workdir: Path) -> None:
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True)
    (workdir / "calc.py").write_text(CALC)
    for cmd in (["init", "-q"], ["add", "-A"], ["-c", "user.name=bench", "-c",
                                                 "user.email=bench@localhost", "commit", "-qm", "start"]):
        subprocess.run(["git", "-C", str(workdir), *cmd], check=True)
    # Harness files written into the workspace stay out of the diff.
    (workdir / ".git" / "info" / "exclude").write_text("mtime-test-*/\n.claude/\n.econocontext/\n")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--harness", choices=sorted(HARNESSES), default="claude-code")
    p.add_argument("--label", default=None, help="default: smoke-<harness>")
    p.add_argument("--arm", choices=["baseline", "econo"], default="econo")
    p.add_argument("--mode", choices=["observe"], default="observe")
    p.add_argument("--max-minutes", type=float, default=5)
    p.add_argument("--server", default="http://127.0.0.1:6767")
    p.add_argument("--gateway", default="http://127.0.0.1:8787")
    a = p.parse_args()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    run_id = f"{a.label or 'smoke-' + a.harness}:{a.arm}:calc-{stamp}"
    workdir = HOME / "data" / "work" / re.sub(r"[^\w.-]", "_", run_id)
    make_repo(workdir)
    host, overrides = HARNESSES[a.harness]
    register_run(run_id, a.arm, a.mode, f"{a.harness}-smoke", workdir=str(workdir),
                 host=host, overrides=overrides)
    spec = write_spec(a.harness, run_id, workdir, a.gateway, a.arm == "econo")
    print(f"== {run_id}", flush=True)

    status, reply = "done", ""
    try:
        reply, _ = asyncio.run(run_session(a.server, spec, workdir, TASK, a.max_minutes * 60,
                                           approve=True, native=a.harness == "claude-code"))
    except TimeoutError:
        status = "timeout"
    except Exception as exc:  # report, don't crash: the run row still gets its status
        status = f"error: {str(exc)[:300]}"
    engine, _ = engine_for(run_id)
    engine.end_run(status)
    diff = subprocess.run(["git", "-C", str(workdir), "diff"], capture_output=True, text=True).stdout
    totals = summary(engine.db, run_id)["totals"]
    print(json.dumps({"run_id": run_id, "status": status, "reply": reply[:300], "diff": diff,
                      "calls": totals["calls"], "cost_usd": totals["cost_usd"],
                      "cache_read_share": totals["cache_read_share"]}, indent=1))


if __name__ == "__main__":
    main()
