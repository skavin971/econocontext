"""After a run: was output that an edit removed needed again later?

Rewritten from econocontext/learn/evidence_labels.py (feature/claude-code) for one
trial's econo.sqlite. For every obs ID an applied edit removed from the context:

  fetched           `econo get N` was called for it after the edit
  rerun             the same command was run again after the edit
  file_reread       a file that output had read was read again after the edit
  needed_again      any of the three

One label per (edit, removed obs). Pure over the run store's rows; writes nothing.
"""

import json
import re

from .run_store import RunStore

GET = re.compile(r"^\s*(\d+)\b")


def removed_output_labels(store: RunStore) -> list[dict]:
    obs = {r["obs_id"]: r for r in store.rows("SELECT * FROM observations")}
    files = store.rows("SELECT * FROM files")
    ops = store.rows("SELECT * FROM econo_ops WHERE op='get'")
    out = []
    for e in store.rows("SELECT * FROM edits ORDER BY turn"):
        for oid in json.loads(e["removed_obs"] or "[]"):
            o = obs.get(oid)
            if o is None:
                continue
            fetched = any(m and int(m.group(1)) == oid and (op["ts"] or 0) >= (e["ts"] or 0)
                          for op in ops for m in [GET.match(op["args"] or "")])
            rerun = any(x["turn"] > e["turn"] and x["command"] == o["command"]
                        for x in obs.values())
            paths = {f["path"] for f in files if f["obs_id"] == oid}
            file_reread = any(f["path"] in paths and f["turn_read"] > e["turn"] for f in files)
            out.append({"edit_turn": e["turn"], "obs_id": oid, "fetched": fetched,
                        "rerun": rerun, "file_reread": file_reread,
                        "needed_again": fetched or rerun or file_reread})
    return out
