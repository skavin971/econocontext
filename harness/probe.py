"""Can EconoContext see, and address, the sub-agents a stock harness starts itself?

Why it exists: worker placement (continue or resume a worker) needs workers that can be
told apart and addressed. Whether they can is a question for evidence, not for the
harness's source. This runs ONE task and collects, side by side:

  omnigent   the session's items (tool calls as the harness reported them) and child sessions
  gateway    the model calls, grouped by context_key (one per conversation loop) and model
  harness    the harness's own session files (Gemini: HOME/.gemini; Claude Code:
             ~/.claude/projects/<workspace>/), names and sizes only

Gemini CLI gets a natural question (it delegated on its own: docs/omnigent-findings.md).
Claude Code is asked to use a sub-agent and then continue that same one (SendMessage),
because the question for it is whether continuing works, not whether it delegates.
openai-controlled (our openai-agents harness on Gemini, explore and general workers)
gets a natural question and a follow-up; with --force RESUME or HANDOFF in autopilot,
the follow-up's delegation shows whether EconoContext's placement reaches the worker.

It prints the evidence as JSON; the answers are written up in docs/omnigent-findings.md.
One run on purpose: the API is rate limited.

Run: .venv/bin/python harness/probe.py --harness claude-code --repo data/work/c3_econo_pytest-dev__pytest-5809
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
from harness.session import (check_measured, check_settings, latest_call, run_session,  # noqa: E402
                             session_view, write_spec)

from econocontext.config import merge  # noqa: E402
from econocontext.monitor import context_map  # noqa: E402
from omnigent_layer import HOME, claude_workers, engine_for, register_run  # noqa: E402

TASKS = {
    "gemini-omnigent": ("How does this project decide which files, classes and functions to "
                        "collect as tests? Trace the path through the code and name the functions "
                        "involved, with file paths. Do not change any files."),
    "openai-controlled": ("How does this project decide which files, and which functions inside "
                          "them, to collect as tests? Trace the path through the code and name the "
                          "functions involved, with file paths. Do not change any files."),
    "claude-code": ("Use a sub-agent (the Agent tool) to find where this project decides which "
                    "test files to collect, with file paths and function names. Then continue "
                    "that same sub-agent with SendMessage (not a new Agent call) and ask it how "
                    "test functions inside a collected file are found. Reply with both answers. "
                    "Do not change any files."),
}
FOLLOWUPS = {
    "openai-controlled": ("Now: how does a collected test function get its fixtures resolved? Trace "
                          "that path the same way, with file paths and function names. Do not "
                          "change any files."),
}
# Per harness: host name, per-run overrides (caps; Claude Code's price card and budget).
PROBES = {
    "openai-controlled": ("omnigent", {"limits": {"max_model_calls": 80}}),  # root and worker both work
    "gemini-omnigent": ("omnigent:gemini", {"limits": {"max_model_calls": 30}}),
    "claude-code": ("omnigent:claude-code",
                    {"model": {"provider": "anthropic", "name": "claude-sonnet-5"},
                     "allowlist": {"ZONED": False},
                     "limits": {"max_model_calls": 30, "per_instance_budget_usd": 0.60}}),
}


def gateway_view(engine, run_id: str) -> dict:
    loops: dict[str, dict] = {}
    for r in engine.db.rows("SELECT name, metadata FROM runtime_spans WHERE run_id=? AND kind='model' "
                            "ORDER BY started_at", (run_id,)):
        meta = json.loads(r["metadata"] or "{}")
        loop = loops.setdefault(meta.get("context_key", "?"),
                                {"calls": 0, "tools": Counter(), "models": Counter()})
        loop["calls"] += 1
        loop["models"][r["name"]] += 1
        loop["tools"].update(c["name"] for c in meta.get("response_calls") or [])
    return {key: {"calls": v["calls"], "tools": dict(v["tools"]), "models": dict(v["models"])}
            for key, v in loops.items()}


def files_view(harness: str, home: Path, workdir: Path) -> list[dict]:
    """The harness's own session files: names and sizes only."""
    if harness == "openai-controlled":  # its sessions live in Omnigent (see omnigent above)
        return []
    if harness == "claude-code":  # Claude Code keeps transcripts per workspace path
        root = Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(workdir))
        files = sorted(root.rglob("*.jsonl")) if root.exists() else []
        return [{"path": str(f.relative_to(root)), "bytes": f.stat().st_size} for f in files]
    files = sorted((home / ".gemini").rglob("*.json*"))
    return [{"path": str(f.relative_to(home)), "bytes": f.stat().st_size} for f in files
            if f.name != "settings.json"]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--harness", choices=sorted(TASKS), default="claude-code")
    p.add_argument("--repo", required=True, help="a git repository to clone for the task")
    p.add_argument("--task", help="the task text (default: the harness's probe task)")
    p.add_argument("--followup", help="a second message, sent in the same session once the first is done")
    p.add_argument("--label", help="run label (default: probe-<harness>)")
    p.add_argument("--mode", choices=["observe", "autopilot"], default="observe",
                   help="with --resume: observe logs placement, autopilot carries it out")
    p.add_argument("--resume", action="store_true", help="allow RESUME (worker placement)")
    p.add_argument("--force", choices=["FRESH", "RESUME", "HANDOFF"],
                   help="an experiment arm: placement chooses it whenever it is a candidate")
    p.add_argument("--max-minutes", type=float, default=10)
    p.add_argument("--server", default="http://127.0.0.1:6767")
    p.add_argument("--gateway", default="http://127.0.0.1:8787")
    a = p.parse_args()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    run_id = f"{a.label or 'probe-' + a.harness}:econo:subagents-{stamp}"
    safe = re.sub(r"[^\w.-]", "_", run_id)
    workdir = HOME / "data" / "work" / safe
    subprocess.run(["git", "clone", "-q", str(Path(a.repo).resolve()), str(workdir)], check=True)
    host, overrides = PROBES[a.harness]
    if a.resume:
        overrides = merge(overrides, {"allowlist": {"RESUME": True},
                                      "constraints": {"max_quality_risk": 0.2},
                                      "limits": {"max_model_calls": 80, "per_instance_budget_usd": 1.20}})
    if a.force:
        overrides = merge(overrides, {"allowlist": {a.force: True}, "placement": {"force": a.force},
                                      "constraints": {"max_quality_risk": 0.2}})
    # openai-agents workers call the gateway's /current route: this run must be current
    register_run(run_id, "econo", a.mode, f"{a.harness}-probe", workdir=str(workdir),
                 host=host, overrides=overrides, current=a.harness == "openai-controlled")
    spec = write_spec(a.harness, run_id, workdir, a.gateway, True)
    check_settings(a.harness, workdir)
    print(f"== {run_id}", flush=True)

    status, reply, session_id = "done", "", None
    try:
        followup = a.followup or (FOLLOWUPS.get(a.harness) if not a.task else None)
        messages = [a.task or TASKS[a.harness]] + ([followup] if followup else [])
        reply, session_id = asyncio.run(run_session(a.server, spec, workdir, messages,
                                                    a.max_minutes * 60, approve=True,
                                                    native=a.harness == "claude-code",
                                                    activity=latest_call(engine_for(run_id)[0])))
    except TimeoutError:
        status = "timeout"
    except Exception as exc:
        status = f"error: {str(exc)[:300]}"
    engine, _ = engine_for(run_id)
    status = check_measured(engine, status)
    engine.end_run(status)
    if session_id is None:  # the session id is printed by run_session; find it if we timed out
        print("no session id: evidence from the gateway and Gemini's files only")
    evidence = {"run_id": run_id, "status": status, "reply": reply[:500],
                "omnigent": asyncio.run(session_view(a.server, session_id)) if session_id else None,
                "gateway_loops": gateway_view(engine, run_id),
                "workers": (claude_workers.report(engine.db, run_id) if a.harness == "claude-code"
                            else context_map.workers(engine.db, run_id, 300)),
                "placements": [dict(r) for r in engine.db.rows(
                    "SELECT created_at, chosen, applied, payloads FROM decisions WHERE run_id=? "
                    "AND intercept='plan_dispatch' ORDER BY created_at", (run_id,))],
                "harness_files": files_view(a.harness, HOME / "data" / "work" / f"{safe}.home",
                                            workdir)}
    out = HOME / "data" / "runs" / "probe" / f"{safe}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(evidence, indent=1))
    print(json.dumps(evidence, indent=1)[:6000])
    print(f"\nsaved to {out}")


if __name__ == "__main__":
    main()
