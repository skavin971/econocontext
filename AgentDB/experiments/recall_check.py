"""The memory layer on real sessions: ingest archived Tier A sessions in repeat order and, before
each later session goes in, ask recall() about each of its read-only calls at epoch 0. The hits
should reproduce replay_sessions.py, which counted them by hand.

usage: python recall_check.py <sessions dir> <label>      (starts its own Postgres)
"""

import collections
import json
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

import pgserver

from agent_db import PostgresAgentDB
from agent_db.memory import Pool, ingest_session, recall, repeatable


def calls_at_epoch0(path: Path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    bumps = sorted(r["call_no"] for r in db.execute("SELECT call_no FROM decisions WHERE rule='9 invalidate'"))
    for r in db.execute("SELECT call_no, tool, input, output, tokens FROM items ORDER BY id"):
        if sum(1 for b in bumps if b < r["call_no"]) == 0:
            yield r["tool"], json.loads(r["input"] or "{}"), r["output"] or "", r["tokens"] or 0


def main(sessions_dir: Path, label: str):
    server = pgserver.get_server(Path(tempfile.mkdtemp()) / "pg")
    db = PostgresAgentDB(server.get_uri())
    files = sorted(sessions_dir.glob(f"{label}/*/*.sqlite3"))
    by_task = collections.defaultdict(list)
    for f in files:
        m = re.match(r"(.+)-r(\d+)\.sqlite3$", f.name)
        by_task[m.group(1)].append((int(m.group(2)), f))
    totals = collections.Counter()
    for task, runs in sorted(by_task.items()):
        for repeat, path in sorted(runs):
            run_id = f"{label}.{path.parts[-2]}.{task}.r{repeat}"
            if repeat > 1:
                for tool, args, output, tokens in calls_at_epoch0(path):
                    if not repeatable(tool, args):
                        continue
                    totals["epoch0_readonly"] += 1
                    hit = recall(db, Pool(labels=[label]), task, tool, args, epoch=0)
                    if hit:
                        totals["hits"] += 1
                        totals["hit_tokens"] += tokens
                        totals["identical"] += hit.output == output
            ingest_session(db, path, run_id)
            totals["ingested"] += 1
    print(f"{label}: ingested {totals['ingested']} sessions of {len(by_task)} tasks")
    print(f"  epoch-0 read-only calls in later sessions: {totals['epoch0_readonly']}; served by recall: "
          f"{totals['hits']} ({totals['hit_tokens']} tokens); served output identical to what the "
          f"session actually got: {totals['identical']} of {totals['hits']}")
    db.close()
    server.cleanup()


if __name__ == "__main__":
    main(Path(sys.argv[1]), sys.argv[2])
