"""Cross-session memory for EconoContext's Tier A harness, on the Agent DB.

What it does. Each Tier A trial is one session: its own agent, container and session
database, nothing carried over. This layer puts what each session saw into the shared Agent
DB and lets a later session reuse it:

  ingest_session   a finished session's tool results become runs/segments/tool_results/
                   evidence in the shared store, with content identity;
  Pool             which earlier sessions a session may draw from (its experimental condition);
  recall           an identical read-only call, answerable from the pool without running the
                   tool, with where the answer came from;
  MemoryPolicy     the harness policy: wraps any other policy and adds recall, recording and
                   a report of what was served and from which session.

What makes a reuse sound. Every trial of a task starts from the task's container image, so
until a session's first write (its write epoch is 0) every file is byte-identical across
sessions of the same task. A read-only call made at epoch 0 in one session returns exactly what
it returned at epoch 0 in another. That is the only case served here, and it is the case the
archived sessions confirmed: 11 of 11 such outputs identical (experiments/replay_sessions.py).
After a write, identical calls differed half the time; serving them needs the current file's
hash, which a policy cannot see, so they are not served.

What a served output changes. The tool does not run; the model's prompt is the same. Under
Tier A's cost measure (prefill FLOPs) that is zero saving; the saving is tool time, and it is
reported as such. Whether memory can also shorten trajectories is a separate experiment.

Leakage. Reuse within the same task is exactly what a benchmark must not count as the
optimizer's saving (DECISIONS.md, D5), so a Pool names its condition and every reuse records
its source session; a report separates them.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .records import EvidenceEvent, SegmentKind, make_ref, make_segment, sha256

HOST = "tier-a"
AGENT = "main"

# econocontext/session.py (Tier A), verbatim: which calls only read.
READ_ONLY = {"cat", "head", "tail", "ls", "grep", "rg", "find", "wc", "echo", "pwd", "which", "file",
             "stat", "diff", "tree", "du", "df", "env", "printenv", "date", "whoami", "id", "uname",
             "nl", "sort", "uniq", "cut", "tr", "column", "less", "more", "jq"}
READ_ONLY_GIT = {"status", "log", "diff", "show", "branch", "rev-parse", "ls-files", "blame"}
TESTS = re.compile(r"^\s*(python3? -m pytest|pytest|npm test|make test|go test|cargo test)\b")


def read_only_bash(command: str) -> bool:
    if any(mark in command for mark in (">", "tee ", " -i", "--in-place", "$(", "`")):
        return False
    for part in command.replace("&&", "|").replace("||", "|").replace(";", "|").split("|"):
        words = part.strip().split()
        if not words:
            continue
        if words[0] == "git" and len(words) > 1 and words[1] in READ_ONLY_GIT:
            continue
        if words[0] == "sed" and "-n" in words:
            continue
        if words[0] not in READ_ONLY:
            return False
    return True


def repeatable(tool: str, args: dict) -> bool:
    command = args.get("command", "")
    return tool == "read_file" or (tool == "bash" and (read_only_bash(command) or bool(TESTS.match(command))))


def writes(tool: str, args: dict) -> bool:
    return tool == "write_file" or (tool == "bash" and not repeatable(tool, args))


def tool_key(tool: str, args: dict) -> str:
    """Identity of a call, as Tier A's session store computes it."""
    args = {k: v for k, v in (args or {}).items() if k != "description"}
    return hashlib.sha256(json.dumps([tool, args], sort_keys=True).encode()).hexdigest()[:24]


def parse_run_id(run_id: str) -> dict:
    """Tier A run ids are <label>.<arm>.<task>.r<n>."""
    m = re.match(r"^(?P<label>[^.]+)\.(?P<arm>[^.]+)\.(?P<task>.+)\.r(?P<repeat>\d+)$", run_id)
    return m.groupdict() if m else dict(label=None, arm=None, task=run_id, repeat=None)


def read_set_for(tool: str, args: dict, output: str, epoch: int, task: str) -> dict[str, str]:
    """What a call's output depended on, with content identity where it is known."""
    if tool == "read_file" and epoch == 0:
        return {str(args.get("path") or args.get("file_path") or ""): "sha256:" + sha256(output)}
    if tool == "read_file":
        return {str(args.get("path") or args.get("file_path") or ""): "text:" + sha256(output),
                "*": f"epoch:{epoch}"}
    if epoch == 0:
        return {"*": f"image:{task}"}
    return {"*": f"epoch:{epoch}"}


def shareable(read_set: dict[str, str]) -> bool:
    """A read set whose every entry is a content identity, not a per-session counter."""
    return all(v.startswith(("sha256:", "image:")) for v in read_set.values())


# -- recording one session into the shared store -----------------------------------------

class Recorder:
    """Writes a session's calls into the Agent DB as they happen (MemoryPolicy) or after the
    fact (ingest_session). One per session."""

    def __init__(self, db, run_id: str, task: str, label: str | None, arm: str | None,
                 model: str = "unknown"):
        self.db, self.run_id, self.task = db, run_id, task
        self.agent = f"{run_id}:{AGENT}"
        self.epoch = 0
        self.calls = 0
        db.start_run(run_id, HOST, task, arm or "unknown", "observe", model, 0.7, label or "")
        from .records import AgentNode
        db.upsert_agent(run_id, AgentNode(self.agent, None, None))

    def record(self, tool: str, args: dict, output: str, call_no: int | None = None) -> str:
        """One tool result. Returns the tool result id. Bumps the epoch after a write."""
        call_no = self.calls if call_no is None else call_no
        epoch = self.epoch
        read_set = read_set_for(tool, args, output, epoch, self.task)
        source = next((k for k in read_set if k != "*"), None)
        segment = make_segment(self.run_id, self.agent, f"call:{call_no}:{tool_key(tool, args)}",
                               SegmentKind.TOOL_RESULT, output, role="tool", source=source,
                               version=read_set.get(source) if source else None)
        self.db.add_segment(segment)
        self.db.add_tool_result(segment.id, self.run_id, self.agent, tool, tool_key(tool, args),
                                segment.id, read_set, not repeatable(tool, args))
        events = []
        if tool == "read_file":
            ref = make_ref("file", source, read_set[source], output, recoverable=epoch == 0)
            events.append(EvidenceEvent("acquired", tool, tool_key(tool, args), source, ref))
        else:
            ref = make_ref("search", f"bash:{tool_key(tool, args)}", read_set["*"], output,
                           recoverable=epoch == 0)
            events.append(EvidenceEvent("acquired", tool, tool_key(tool, args), "*", ref))
        if writes(tool, args):
            events.append(EvidenceEvent("mutated", tool, tool_key(tool, args), "*"))
        self.db.add_evidence_events(self.run_id, self.agent, call_no, events)
        if writes(tool, args):
            self.epoch += 1
        self.calls = call_no + 1
        return segment.id

    def finish(self, status: str = "done", passed: bool | None = None) -> None:
        self.db.end_run(self.run_id, status)
        if passed is not None:
            self.db.set_resolved(self.run_id, passed)


def ingest_session(db, path: str | Path, run_id: str, passed: bool | None = None,
                   model: str = "unknown") -> dict:
    """A finished Tier A session (session.sqlite3) into the shared store. Returns counts."""
    ids = parse_run_id(run_id)
    rec = Recorder(db, run_id, ids["task"], ids["label"], ids["arm"], model)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    bumps = sorted(r["call_no"] for r in conn.execute(
        "SELECT call_no FROM decisions WHERE rule='9 invalidate'"))
    n = 0
    for r in conn.execute("SELECT id, call_no, tool, input, output FROM items ORDER BY id"):
        args = json.loads(r["input"] or "{}")
        # The epoch at the time of the call: writes logged at earlier calls.
        rec.epoch = sum(1 for b in bumps if b < r["call_no"])
        rec.record(r["tool"], args, r["output"] or "", call_no=r["call_no"])
        n += 1
    rec.finish("done", passed)
    conn.close()
    return dict(run_id=run_id, items=n, task=ids["task"])


# -- recalling from earlier sessions ------------------------------------------------------

@dataclass
class Pool:
    """Which earlier sessions a session may draw from. This is the experimental condition, so
    it is explicit: the run-id labels to search, whether the same task counts, and whether only
    sessions that passed count."""
    labels: list[str]
    same_task: bool = True
    only_passed: bool = False
    exclude: set[str] = field(default_factory=set)

    def runs(self, db, task: str) -> list[dict]:
        """The sessions this pool admits, oldest first. Tier A run ids are '<label>.<arm>.<task>.r<n>',
        so sessions are selected by host (and task), then by the label in the id."""
        out = []
        for run in db.runs_of_host(HOST, task if self.same_task else None):
            ids = parse_run_id(run["run_id"])
            if run["run_id"] in self.exclude or ids["label"] not in self.labels:
                continue
            if (run["instance_id"] == task) != self.same_task:
                continue
            if self.only_passed and not run["resolved"]:
                continue
            out.append(run)
        return out


@dataclass
class Recall:
    output: str
    run_id: str
    tool_result_id: str
    tokens: int


def recall(db, pool: Pool, task: str, tool: str, args: dict, epoch: int) -> Recall | None:
    """The output an earlier session got for this exact call, if that is provably what running
    it now would return: the call only reads, this session has written nothing yet, and the
    earlier result's read set is content-identified (so it too was taken before any write)."""
    if epoch != 0 or not repeatable(tool, args):
        return None
    key = tool_key(tool, args)
    for run in pool.runs(db, task):
        if not pool.same_task:
            continue  # across tasks the containers differ; nothing is provably identical yet
        # Every result of this call in that session, newest first: a session that repeated the
        # call after writing has an unshareable newer copy and a shareable older one.
        for row in reversed(db.tool_results_with_text(run["run_id"])):
            if row["tool_name"] != tool or row["args_key"] != key or row.get("side_effect"):
                continue
            read_set = row["read_set"] if isinstance(row["read_set"], dict) else json.loads(row["read_set"])
            if shareable(read_set):
                text = row["text"]
                return Recall(text, run["run_id"], row["tool_result_id"], len(text.encode()) // 4)
    return None


# -- the harness policy --------------------------------------------------------------------

class MemoryPolicy:
    """A Tier A harness policy: `inner` (Base, Econo, ...) decides as before; this adds recall
    from the pool before a tool runs, records every result into the shared store, and reports
    what was served and from where.

    Construction, as the runner passes policy_args: MemoryPolicy(store=<DSN or 'neo4j'>,
    labels=[...], same_task=True, only_passed=False, inner='baselines.base.policy:Base').
    """

    name = "memory"

    def __init__(self, store: str | None = None, labels: list[str] | None = None,
                 same_task: bool = True, only_passed: bool = False, inner=None, db=None):
        self.db = db or open_store(store)
        self.pool = Pool(labels or [], same_task, only_passed)
        self.inner = load_policy(inner) if isinstance(inner, str) else inner
        self.budget = getattr(self.inner, "budget", "allowed")
        self.recorder = None
        self.served: list[dict] = []
        self._pending = None

    def start(self, instruction: str, harness) -> None:
        run_id = getattr(harness, "run_id", None) or "memory.unknown.task.r0"
        ids = parse_run_id(run_id)
        self.pool.exclude.add(run_id)
        self.recorder = Recorder(self.db, run_id, ids["task"], ids["label"], ids["arm"],
                                 getattr(harness, "model", "unknown"))
        if self.inner:
            self.inner.start(instruction, harness)

    async def before_call(self, messages, harness):
        return await self.inner.before_call(messages, harness) if self.inner else messages

    def before_tool(self, name: str, args: dict) -> str | None:
        self._pending = None
        if self.inner:
            served = self.inner.before_tool(name, args)
            if served is not None:
                return served
        hit = recall(self.db, self.pool, self.recorder.task, name, args, self.recorder.epoch)
        if hit is None:
            return None
        self._pending = hit
        self.served.append(dict(call=self.recorder.calls, tool=name, source_run=hit.run_id,
                                tool_result_id=hit.tool_result_id, tokens=hit.tokens))
        return hit.output

    def after_tool(self, name: str, args: dict, output: str, served: bool, call_id=None) -> str:
        self.recorder.record(name, args, output)
        if self.inner:
            return self.inner.after_tool(name, args, output, served, call_id)
        return output

    def report(self) -> dict | None:
        inner = self.inner.report() if self.inner else None
        if self.recorder:
            self.recorder.finish("done")
        return dict(inner=inner, pool=dict(labels=self.pool.labels, same_task=self.pool.same_task,
                                          only_passed=self.pool.only_passed),
                    served=self.served, served_calls=len(self.served),
                    served_tokens=sum(s["tokens"] for s in self.served))


def open_store(store: str | None):
    """A DSN opens Postgres; 'neo4j' opens Neo4j from the environment; the default is the
    environment's AGENT_DB_DSN."""
    import os
    store = store or os.environ.get("AGENT_DB_DSN") or os.environ.get("AGENT_DB", "")
    if store == "neo4j":
        from .neo4j_store import Neo4jAgentDB
        return Neo4jAgentDB()
    if not store:
        raise ValueError("no store: pass store=<postgres DSN> or 'neo4j', or set AGENT_DB_DSN")
    from .postgres import PostgresAgentDB
    return PostgresAgentDB(store)


def load_policy(spec: str):
    """'module:Class' -> an instance with no arguments."""
    import importlib
    module, cls = spec.split(":")
    return getattr(importlib.import_module(module), cls)()
