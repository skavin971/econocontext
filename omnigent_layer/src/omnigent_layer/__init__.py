"""The Omnigent layer: connects Omnigent (the platform) to EconoContext (the optimizer).

Why it exists: Omnigent runs the agents; EconoContext decides. This package is the
only code that knows both. It has two entry points, and they share one Agent DB:

  gateway.py   every model call (full prompt, exact usage)       -> plan_prompt, record
  policy.py    every tool call and result (Omnigent policies)    -> tool intercepts, write barrier

Runs are registered before they start (`register_run`), so both entry points know a
run's arm and mode from its id alone.
What it must never do: change the core, or let its own failure change what Omnigent does.
"""

import hashlib
import json
import os
import threading
from pathlib import Path

from econocontext.engine import EconoContext
from econocontext.learn.predictors import History
from econocontext.store.db import AgentDB

from .workspace import OmnigentHost

# The repo root holds config/ and data/. Override with ECONOCONTEXT_HOME.
HOME = Path(os.environ.get("ECONOCONTEXT_HOME") or Path(__file__).resolve().parents[3])
CONFIG_DIR = HOME / "config"
DB_PATH = HOME / "data" / "econocontext.sqlite3"
CURRENT = HOME / "data" / "current_run"   # the run sub-agents' calls belong to
ARMS = ("baseline", "econo")

_engines: dict[str, tuple[EconoContext, str]] = {}  # run_id -> (engine, arm)
_lock = threading.Lock()


def register_run(run_id: str, arm: str, mode: str, instance_id: str | None = None,
                 jev: bool = False, current: bool = False, overrides: dict | None = None,
                 workdir: str | None = None) -> EconoContext:
    """Create the run's row. Call once, before the run's first model call.

    current=True also marks it as the run that /current/... gateway calls belong to
    (sub-agents). Runs marked current must run one at a time.
    `overrides` are per-run config changes (e.g. an allowlist); the key "learned": true
    gives the engine learned H and p (learn/predictors.py) instead of the config guesses.
    """
    if arm not in ARMS:
        raise ValueError(f"arm must be one of {ARMS}, not {arm!r}")
    engine = _engine(run_id, arm, mode if arm == "econo" else "measure", instance_id, jev,
                     overrides, workdir)
    with _lock:
        _engines[run_id] = (engine, arm)
    if current:
        CURRENT.parent.mkdir(parents=True, exist_ok=True)
        CURRENT.write_text(run_id)
    return engine


def current_run() -> str | None:
    return CURRENT.read_text().strip() if CURRENT.exists() else None


def run_row(run_id: str) -> dict | None:
    rows = AgentDB(DB_PATH).rows("SELECT * FROM runs WHERE run_id=?", (run_id,))
    return dict(rows[0]) if rows else None


def engine_for(run_id: str) -> tuple[EconoContext, str] | None:
    """The engine for a registered run, and its arm. None if the run was never registered."""
    with _lock:
        if run_id in _engines:
            return _engines[run_id]
    row = run_row(run_id)
    if row is None:
        return None
    engine = _engine(run_id, row["arm"], row["mode"], row["instance_id"], bool(row["jev"]),
                     json.loads(row["overrides"]) if row["overrides"] else None, row["workdir"])
    with _lock:
        return _engines.setdefault(run_id, (engine, row["arm"]))


def _engine(run_id, arm, mode, instance_id, jev, overrides, workdir) -> EconoContext:
    """One engine for a run, the same in every process (gateway, Omnigent's server)."""
    from .research import recorder_for

    overrides = dict(overrides or {})
    learned = overrides.pop("learned", False)
    history = History(AgentDB(DB_PATH), exclude_instance=instance_id) if learned else None
    return EconoContext(str(CONFIG_DIR), OmnigentHost(workdir) if workdir else None, run_id,
                        host_name="omnigent", arm=arm, instance_id=instance_id,
                        db_path=str(DB_PATH), mode=mode, jev=jev,
                        overrides=overrides or None, workdir=workdir, history=history,
                        observer=recorder_for(run_id))


def agent_id(run_id: str, agent: str | None, instance_key: str | None = None) -> str:
    """One id per agent in a run: '<run_id>:root' for the main agent; a named agent is
    '<run_id>:<name>:<hash of instance_key>' so two workers of one type stay apart."""
    if not agent:
        return f"{run_id}:root"
    if instance_key:
        return f"{run_id}:{agent}:{hashlib.sha256(instance_key.encode()).hexdigest()[:8]}"
    return f"{run_id}:{agent}"
