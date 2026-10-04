"""Loading one day's runs for the analysis scripts. Read-only.

Layout (bench/tblite/run.py):
  runs/<date>/gateway.sqlite                    the gateway's ledger (all runs)
  runs/<date>/<run_id>/command.txt, harbor.log  per run
  runs/<date>/<run_id>/econo.sqlite             EconoCLM runs only
  runs/<date>/harbor/<run_id>/result.json       Harbor's TrialResult (reward, timing)
  runs/<date>/harbor/<run_id>/agent/            CLM's logs_dir: usage.json, timing.json,
                                                trajectory.json, context_snapshots/

Per-call contexts: CLM writes context_snapshots/turn-NNNN.json; the snapshots with
"kind": "agent" are the exact message lists sent, one per model call, in order.
Calls are matched to the ledger's successful (HTTP 200) rows in order.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..core.gateway_ledger import Ledger
from ..core.run_store import RunStore
from ..core.tokenizer import use_bundled_tokenizer

# finish_reason values after which the call is resent with the same prompt below CLM's
# step counter: the gateway sees one more call than CLM counts.
RETRY_REASONS = {"malformed_function_call"}

# Vertex's 400 for a request that ends with an assistant turn: CLM's rebuild after an edit
# can produce one (Gate 4, scan-linux-persistence-artifacts); CLM retries it and fails.
REBUILD_REJECTED = "Requests ending with a model turn are not supported"

use_bundled_tokenizer()  # count tokens exactly as the runs did, on any machine


@dataclass
class Run:
    run_id: str
    arm: str
    task: str
    rep: int
    dir: Path
    trial_dir: Path | None
    result: dict = field(default_factory=dict)
    usage: dict = field(default_factory=dict)       # CLM usage.json
    calls: list[dict] = field(default_factory=list)  # ledger rows, HTTP 200 only, in order
    all_rows: list[dict] = field(default_factory=list)  # every ledger row (incl. failures)

    @property
    def reward(self) -> float | None:
        vr = (self.result or {}).get("verifier_result") or {}
        rewards = vr.get("rewards") or {}
        return rewards.get("reward") if rewards else None

    @property
    def exception(self) -> str | None:
        exc = (self.result or {}).get("exception_info")
        return exc.get("exception_type") if exc else None

    @property
    def exception_message(self) -> str:
        exc = (self.result or {}).get("exception_info")
        return str(exc.get("exception_message") or "") if exc else ""

    @property
    def rebuild_rejected(self) -> bool:
        """The trial died because Vertex refused a request CLM's rebuild ended with an
        assistant turn ("Requests ending with a model turn are not supported")."""
        return REBUILD_REJECTED in self.exception_message

    @property
    def wall_s(self) -> float | None:
        t = (self.result or {}).get("agent_execution") or {}
        try:
            a = datetime.fromisoformat(t["started_at"])
            b = datetime.fromisoformat(t["finished_at"])
        except (KeyError, TypeError, ValueError):
            return None
        return (b - a).total_seconds()

    def store(self) -> RunStore | None:
        path = self.dir / "econo.sqlite"
        return RunStore(path) if path.exists() else None

    def agent_dir(self) -> Path | None:
        return self.trial_dir / "agent" if self.trial_dir else None

    def snapshots(self) -> list[list[dict]]:
        """The message list of every model call, in call order."""
        d = self.agent_dir()
        if not d or not (d / "context_snapshots").is_dir():
            return []
        snaps = []
        for p in sorted((d / "context_snapshots").glob("turn-*.json")):
            data = json.loads(p.read_text())
            if data.get("kind") == "agent":
                snaps.append((data["step"], data["messages"]))
        return [m for _, m in sorted(snaps, key=lambda s: s[0])]

    def trajectory(self) -> list[dict]:
        d = self.agent_dir()
        p = d / "trajectory.json" if d else None
        return json.loads(p.read_text()) if p and p.exists() else []


def parse_run_id(run_id: str) -> tuple[str, str, int]:
    arm, rest = run_id.split("-", 1)
    task, rep = rest.rsplit("-r", 1)
    return arm, task, int(rep)


def find_trial(day: Path, run_id: str) -> Path | None:
    """Harbor's trial folder for this run (named by --trial-name)."""
    base = day / "harbor"
    exact = base / run_id
    if (exact / "result.json").exists():
        return exact
    found = sorted(base.glob(f"{run_id}*/result.json")) if base.exists() else []
    return found[-1].parent if found else None


def load_runs(day: Path, arms: set[str] | None = None) -> list[Run]:
    ledger = Ledger(day / "gateway.sqlite") if (day / "gateway.sqlite").exists() else None
    runs = []
    for d in sorted(p for p in day.iterdir() if p.is_dir() and (p / "command.txt").exists()):
        arm, task, rep = parse_run_id(d.name)
        if arms and arm not in arms:
            continue
        trial = find_trial(day, d.name)
        run = Run(d.name, arm, task, rep, d, trial)
        if trial:
            run.result = json.loads((trial / "result.json").read_text())
            usage = trial / "agent" / "usage.json"
            run.usage = json.loads(usage.read_text()) if usage.exists() else {}
        if ledger:
            run.all_rows = ledger.calls(d.name)
            run.calls = [r for r in run.all_rows if r["http_status"] == 200]
        runs.append(run)
    return runs
