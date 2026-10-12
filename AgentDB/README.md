# agent-db (this copy)

One agent's context on Postgres, for EconoContext's Owner (`econocontext/owner.py`). This copy is trimmed from the upstream version; `CHANGES.md` lists every change, and `DECISIONS.md` is the upstream design log, unchanged.

## What it holds
- `runs`: a run id, used once, and its task.
- `segments`: every message of the conversation (and each tool call, and each tool result in full), write-once, each with its exact token count as the caller gives it; large texts also go to `blobs`.
- `window_entries`: the window, one row per segment shown, with its position, its part within the message, its form (FULL, SLICE, PREVIEW, NOTE, EVICTED or SUMMARY) and the token count of that form.
- `tool_results`: each tool result's call (tool, arguments and their key), whether it may have written, the workspace version it read, and whether it is still valid.
- `source_versions`: the workspace version; `bump` advances it and invalidates every earlier read.

It holds no cost, price or FLOP.

## Operations
`PostgresAgentDB(dsn)`: `start_run`, `get_run`, `add_segment`, `segment`, `set_window`, `window`, `window_results`, `add_tool_result`, `find_tool_result`, `current_versions`, `bump`, `invalidate`. The schema is applied on construction (`CREATE ... IF NOT EXISTS`).

## Running it
From the repository root: `pip install -e ".[agentdb]" -e AgentDB` (psycopg and pgserver). For real runs, `docker compose up -d` starts Postgres 16 and `AGENTDB_DSN` in `.env` points to it. Tests start their own Postgres from pip (pgserver): `.venv/bin/python -m pytest -q AgentDB/tests`.
