"""One trial's own database: runs/<date>/<run_id>/econo.sqlite.

Written only by that trial's EconoClmAgent (one process). The analysis scripts join
it with gateway.sqlite by run_id. "turn" everywhere = the number of the command
in the run (1, 2, 3, ...).

  observations   every command output, stored in full (host_path) with its ID
  files          every file read we detected, with its sha1 at read time
  edits          every applied context edit and the quote we showed for it
  status_lines   the [econo] line shown after each command
  hook_errors    anything our hooks raised (the original result was used instead)
  econo_ops      the model's own `econo` calls, loaded from the container log at the end

ts columns (epoch seconds) let analysis order econo calls against commands and edits.
"""

import time
from pathlib import Path

from .sqlite_util import Store

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
  obs_id INTEGER PRIMARY KEY, turn INTEGER, command TEXT, host_path TEXT,
  bytes INTEGER, sha1 TEXT, cut_in_context INTEGER, ts REAL
);
CREATE TABLE IF NOT EXISTS files (
  path TEXT, sha1 TEXT, turn_read INTEGER, obs_id INTEGER
);
CREATE TABLE IF NOT EXISTS edits (
  turn INTEGER, before_tokens INTEGER, after_tokens INTEGER, first_change_msg INTEGER,
  prefix_tokens_p INTEGER, cached_c INTEGER, predicted_reprocess_R INTEGER,
  predicted_cost_usd REAL, saving_per_call_usd REAL, payoff_calls REAL, removed_obs TEXT,
  calibration REAL, text TEXT, ts REAL
);
CREATE TABLE IF NOT EXISTS status_lines (
  turn INTEGER, text TEXT, stale_paths TEXT, ts REAL
);
CREATE TABLE IF NOT EXISTS hook_errors (
  turn INTEGER, "where" TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS econo_ops (
  ts REAL, op TEXT, args TEXT
);
"""


class RunStore(Store):
    def __init__(self, path: str | Path):
        super().__init__(path, SCHEMA)

    def insert(self, table: str, **fields) -> None:
        if table in ("observations", "edits", "status_lines"):
            fields.setdefault("ts", time.time())
        cols = ", ".join(f'"{c}"' for c in fields)
        self.execute(f"INSERT INTO {table} ({cols}) VALUES ({', '.join('?' * len(fields))})",
                     tuple(fields.values()))
