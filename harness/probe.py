"""Can EconoContext see, and address, the sub-agents stock Gemini CLI starts itself?

Why it exists: worker placement (continue or resume a worker) needs workers that the
platform exposes. Gemini runs its built-in sub-agents (codebase_investigator, ...)
in their own loops, but whether Omnigent sees them as sessions it can address is a
question for evidence, not for its source. This runs ONE natural task likely to make
Gemini delegate (the sub-agent is not named) and collects, side by side:

  omnigent   the session's items (tool calls as ACP reported them) and child sessions
  gateway    the model calls, grouped by context_key (one per conversation loop)
  gemini     Gemini's own session files, under the run's HOME/.gemini

It prints the evidence as JSON; the answers are written up in docs/omnigent-findings.md.
One run on purpose: the API is rate limited.

Run: .venv/bin/python harness/probe.py --repo data/work/g1_econo_pytest-dev__pytest-5809
"""

import argparse
import asyncio
import json
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness.session import run_session, session_view, write_spec  # noqa: E402

from omnigent_layer import HOME, engine_for, register_run  # noqa: E402

TASK = ("How does this project decide which files, classes and functions to collect as "
        "tests? Trace the path through the code and name the functions involved, with "
        "file paths. Do not change any files.")


def gateway_view(engine, run_id: str) -> dict:
    loops: dict[str, dict] = {}
    for r in engine.db.rows("SELECT metadata FROM runtime_spans WHERE run_id=? AND kind='model' "
                            "ORDER BY started_at", (run_id,)):
        meta = json.loads(r["metadata"] or "{}")
        loop = loops.setdefault(meta.get("context_key", "?"), {"calls": 0, "tools": Counter()})
        loop["calls"] += 1
        loop["tools"].update(c["name"] for c in meta.get("response_calls") or [])
    return {key: {"calls": v["calls"], "tools": dict(v["tools"])} for key, v in loops.items()}


def gemini_view(home: Path) -> list[dict]:
    files = sorted((home / ".gemini").rglob("*.json*"))
    return [{"path": str(f.relative_to(home)), "bytes": f.stat().st_size} for f in files
            if f.name != "settings.json"]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--repo", required=True, help="a git repository to clone for the task")
    p.add_argument("--max-minutes", type=float, default=10)
    p.add_argument("--server", default="http://127.0.0.1:6767")
    p.add_argument("--gateway", default="http://127.0.0.1:8787")
    a = p.parse_args()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    run_id = f"probe-gemini:econo:subagents-{stamp}"
    safe = re.sub(r"[^\w.-]", "_", run_id)
    workdir = HOME / "data" / "work" / safe
    subprocess.run(["git", "clone", "-q", str(Path(a.repo).resolve()), str(workdir)], check=True)
    register_run(run_id, "econo", "observe", "gemini-probe", workdir=str(workdir),
                 host="omnigent:gemini", overrides={"limits": {"max_model_calls": 30}})
    spec = write_spec("gemini-omnigent", run_id, workdir, a.gateway, True)
    print(f"== {run_id}", flush=True)

    status, reply, session_id = "done", "", None
    try:
        reply, session_id = asyncio.run(run_session(a.server, spec, workdir, TASK,
                                                    a.max_minutes * 60, approve=True))
    except TimeoutError:
        status = "timeout"
    except Exception as exc:
        status = f"error: {str(exc)[:300]}"
    engine, _ = engine_for(run_id)
    engine.end_run(status)
    if session_id is None:  # the session id is printed by run_session; find it if we timed out
        print("no session id: evidence from the gateway and Gemini's files only")
    evidence = {"run_id": run_id, "status": status, "reply": reply[:500],
                "omnigent": asyncio.run(session_view(a.server, session_id)) if session_id else None,
                "gateway_loops": gateway_view(engine, run_id),
                "gemini_files": gemini_view(HOME / "data" / "work" / f"{safe}.home")}
    out = HOME / "data" / "runs" / "probe" / f"{safe}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(evidence, indent=1))
    print(json.dumps(evidence, indent=1)[:6000])
    print(f"\nsaved to {out}")


if __name__ == "__main__":
    main()
