"""The Omnigent layer: connects Omnigent (the platform) to EconoContext (the optimizer).

Why it exists: Omnigent runs the agents; EconoContext decides. This package is the
only code that knows both. It has two entry points, and they share one Agent DB:

  gateway.py   every model call (full prompt, exact usage)       -> plan_prompt, record
  policy.py    every tool call and result (Omnigent policies)    -> tool intercepts, write barrier

Runs are registered before they start (`register_run`), so both entry points know a
run's arm and mode from its id alone.
What it must never do: change the core, or let its own failure change what Omnigent does.
"""

import os
import threading
from pathlib import Path

from econocontext.engine import EconoContext
from econocontext.store.db import AgentDB

# The repo root holds config/ and data/. Override with ECONOCONTEXT_HOME.
HOME = Path(os.environ.get("ECONOCONTEXT_HOME") or Path(__file__).resolve().parents[3])
CONFIG_DIR = HOME / "config"
DB_PATH = HOME / "data" / "econocontext.sqlite3"
ARMS = ("baseline", "econo")

_engines: dict[str, tuple[EconoContext, str]] = {}  # run_id -> (engine, arm)
_lock = threading.Lock()


def register_run(run_id: str, arm: str, mode: str, instance_id: str | None = None,
                 jev: bool = False) -> EconoContext:
    """Create the run's row. Call once, before the run's first model call."""
    if arm not in ARMS:
        raise ValueError(f"arm must be one of {ARMS}, not {arm!r}")
    engine = EconoContext(str(CONFIG_DIR), None, run_id, host_name="omnigent", arm=arm,
                          instance_id=instance_id, db_path=str(DB_PATH),
                          mode=mode if arm == "econo" else "measure", jev=jev)
    with _lock:
        _engines[run_id] = (engine, arm)
    return engine


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
    engine = EconoContext(str(CONFIG_DIR), None, run_id, host_name="omnigent", arm=row["arm"],
                          instance_id=row["instance_id"], db_path=str(DB_PATH), mode=row["mode"],
                          jev=bool(row["jev"]))
    with _lock:
        return _engines.setdefault(run_id, (engine, row["arm"]))


def agent_id(run_id: str, agent: str | None) -> str:
    """One id per agent in a run: '<run_id>:root' for the main agent, '<run_id>:<name>' otherwise."""
    return f"{run_id}:{agent or 'root'}"
