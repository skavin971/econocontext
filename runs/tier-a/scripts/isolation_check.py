"""Isolation check (2026-10-16): two EconoContext trials back to back on the same task cannot see each other's state
in the agent DB, and the cache simulation starts empty per run. No behaviour changes; no model calls.

Two trials of the same scripted conversation (tests/econocontext/scenarios.py "repeats_and_reads": our harness, a
scripted model, a local shell, EconoContext in prior mode, so no Jev or model calls), with the SAME harness run id
and the same task, against the live agent DB (AGENTDB_DSN). Every read method of PostgresAgentDB is wrapped to record
the run id it was asked for and the run id of every row it returned. Before trial 2's first call, its window, tool
results, versions and the lookup that rule 1 uses (find_tool_result for the repeated `cat data.txt`) are probed.
Then: trial 2 must make the same decisions and send the same requests as trial 1 (nothing carried over), and every
row read in trial 2 must belong to trial 2's run.

Cache simulation: measure.cache_sim.cached_prefix over each trial's own prompts (trial 2's first call must have R = 0),
and over the two trials concatenated (which would match: what keeps runs apart is one log per run).

Use: PYTHONPATH=. .venv/bin/python runs/tier-a/scripts/isolation_check.py
"""

import asyncio
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from agent_db import postgres as pg  # noqa: E402
from econocontext.owner import tool_key  # noqa: E402
from econocontext.policy import agentdb_dsn  # noqa: E402
from harness.agent import Agent  # noqa: E402
from measure.cache_sim import cached_prefix  # noqa: E402
from measure.flops import prompt_ids  # noqa: E402
from measure.models import MEASURED  # noqa: E402
from measure.tokens import tokenizer  # noqa: E402
from tests.econocontext.scenarios import Replies, Scripted, decisions, repeats_and_reads  # noqa: E402
from tests.harness.helpers import FakeModel, LocalEnv  # noqa: E402

READS = []          # (method, run asked for, run ids of the rows returned)


def wrap(name):
    original = getattr(pg.PostgresAgentDB, name)

    def wrapped(self, *args, **kwargs):
        out = original(self, *args, **kwargs)
        asked = args[0] if name != "segment" else None
        rows = out if isinstance(out, list) else [] if out is None else [out]
        got = []
        for r in rows:
            if hasattr(r, "run_id"):
                got.append(r.run_id)
            elif isinstance(r, dict) and "run_id" in r:
                got.append(r["run_id"])
            elif isinstance(out, dict) and name == "current_versions":
                got.append(asked)        # filtered in SQL by run_id; rows carry no run id of their own
                break
        READS.append((name, asked, got, len(rows) if not isinstance(out, dict) or name != "current_versions" else len(out)))
        return out
    setattr(pg.PostgresAgentDB, name, wrapped)


for method in ("window", "window_results", "current_versions", "find_tool_result", "segment", "get_run"):
    wrap(method)
blob_reads = []
original_get = pg.PostgresBlobStore.get
pg.PostgresBlobStore.get = lambda self, key: blob_reads.append(key) or original_get(self, key)


def trial(tmp: Path, n: int, probe=None):
    model = FakeModel(repeats_and_reads(Replies()))
    policy = Scripted(None)                   # the scenario's own settings (no forced rules), as in the goldens
    agent = Agent(logs_dir=tmp / f"logs{n}", model_name="openai/qwen3.8:27b", client=model,
                  state_dir=str(tmp / f"state{n}"), policy=policy, run_id="isolation-check", interface="ours")
    env = LocalEnv(tmp / f"work{n}")
    (tmp / f"work{n}").mkdir()
    context = SimpleNamespace(n_input_tokens=0, n_cache_tokens=0, n_output_tokens=0)
    start = len(READS)

    async def go():
        await agent.setup(env)
        if probe:                       # wrap start so the probe runs right after the run is created
            real_start = policy.start

            def start_and_probe(instruction, harness):
                real_start(instruction, harness)
                probe(policy.owner)
            policy.start = start_and_probe
        await agent.run("Inspect the files and report.", env, context)
    asyncio.run(go())
    return policy, model, READS[start:]


def main() -> None:
    dsn = agentdb_dsn()
    tmp = Path(tempfile.mkdtemp(prefix="isolation-"))
    p1, m1, reads1 = trial(tmp, 1)
    run1 = p1.owner.run
    store = pg.PostgresAgentDB(dsn)
    key = tool_key("bash", {"command": "cat data.txt"})
    print(f"trial 1: run {run1}; requests {len(m1.sent)}; decisions {len(decisions(p1.owner))}")
    print(f"  trial 1's state after it ended: window {len(store.window(run1))} segments, tool results "
          f"{len(store.window_results(run1))} shown, versions {store.current_versions(run1)}, "
          f"find_tool_result(cat data.txt) -> {'a row' if store.find_tool_result(run1, 'bash', key) else 'none'}")
    valid1 = store.rows("SELECT DISTINCT tool_name, args_key FROM tool_results WHERE run_id=%s AND valid AND NOT side_effect",
                        (run1,))
    print(f"  lookups still valid in trial 1 at its end (rule 1 would answer them): {len(valid1)}; "
          f"find_tool_result(run 1) returns a row for {sum(bool(store.find_tool_result(run1, v['tool_name'], v['args_key'])) for v in valid1)}")
    probed = {}

    def probe(owner):
        r = owner.run
        probed.update(run=r, window=len(store.window(r)), results=len(store.window_results(r)),
                      versions=store.current_versions(r), found=store.find_tool_result(r, "bash", key),
                      found_valid1=sum(bool(store.find_tool_result(r, v["tool_name"], v["args_key"])) for v in valid1),
                      same_task_runs=store.rows("SELECT count(*) AS n FROM runs WHERE task = %s", (owner.task,))[0]["n"])
    p2, m2, reads2 = trial(tmp, 2, probe)
    run2 = p2.owner.run
    print(f"trial 2: run {run2}; requests {len(m2.sent)}; decisions {len(decisions(p2.owner))}")
    print(f"  probed right after trial 2 started (before its first call): window {probed['window']}, tool results "
          f"{probed['results']}, versions {probed['versions']}, find_tool_result(cat data.txt) -> "
          f"{'a row' if probed['found'] else 'none'}; the same {len(valid1)} lookups for trial 2: {probed['found_valid1']} rows; "
          f"runs in the DB with this task: {probed['same_task_runs']}")
    print(f"  same harness run id, different agent-DB runs: {run1 != run2}")
    same_decisions = decisions(p1.owner) == decisions(p2.owner)
    same_requests = json.dumps(m1.sent, sort_keys=True, default=str) == json.dumps(m2.sent, sort_keys=True, default=str)
    print(f"  trial 2 made the same decisions as trial 1: {same_decisions}; sent the same requests: {same_requests}")
    by_method = Counter(name for name, *_ in reads2)
    foreign = [(name, asked, got) for name, asked, got, _ in reads2 if any(g != run2 for g in got) or (asked and asked != run2)]
    print(f"  reads in trial 2: {sum(by_method.values())} ({dict(by_method)}); rows returned "
          f"{sum(len(got) for _, _, got, _ in reads2)}; from another run: {len(foreign)}")
    for f in foreign[:5]:
        print("   FOREIGN", f)
    print(f"  blob reads (any trial): {len(blob_reads)}")
    tok = tokenizer(MEASURED)
    ids = lambda m: [prompt_ids(tok, {"messages": msgs, "tools": m.tools[i] if i < len(m.tools) else None})   # noqa: E731
                     for i, msgs in enumerate(m.sent)]
    a, b = ids(m1), ids(m2)
    alone, together = cached_prefix(b), cached_prefix(a + b)[len(a):]
    print(f"cache simulation: trial 2 alone, first call R = {alone[0]} of P = {len(b[0])}; total R {sum(alone)}. "
          f"Trial 2 after trial 1 in ONE simulation, first call R = {together[0]}; total R {sum(together)} "
          f"(what separates them is one calls.jsonl per run id)")
    store.close()


if __name__ == "__main__":
    main()
