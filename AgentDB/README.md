# agent-db

Two Agent DBs built to EconoContext's `AGENT_DB_CONTRACT.md`, one on Postgres and one
on Neo4j. Part A of the contract is `agent_db/schema.sql` (and the constraints in
`agent_db/neo4j_store.py`); Part B is `PostgresAgentDB` and `Neo4jAgentDB`. The SQLite
store in the EconoContext repository (`econocontext/store/db.py`) is the reference they
are checked against. The contract followed is the one on the labmate's `econo-jev`
branch, which adds evidence identity to the version on `main` (DECISIONS.md, D7).

## Running it

```sh
uv venv --python 3.12 venv && source venv/bin/activate
uv pip install -e ".[dev]" -e <path to an EconoContext checkout, branch econo-jev>
pytest -q      # starts its own Postgres; the Neo4j tests skip when no server is reachable
```

The second install is EconoContext itself: the records the operations take (`Segment`,
`AgentNode`, `Decision`, `ProviderUsage`, `EvidenceEvent`) come from `econocontext.types`
and `econocontext.evidence`, and the conformance tests compare against its SQLite store.

Postgres comes from the `pgserver` package (Postgres 16, from pip). To use one that is
already running, pass its DSN: `PostgresAgentDB("postgresql://user:pw@host/db")`. The
schema is applied on construction with `CREATE ... IF NOT EXISTS`. Neo4j takes
`NEO4J_URI` (default `bolt://localhost:7687`) and `NEO4J_AUTH` as `user:password` from
the environment, or the same as arguments: `Neo4jAgentDB(uri, ("neo4j", "pw"))`.

## Using it from EconoContext

The engine builds its store by name (`AgentDB(path)`), so a backend is swapped by
replacing that name before an engine is made, which is what
`upstream_runs/econocontext_backend.py` does for the test suite:

```python
import econocontext.engine as engine
from agent_db import PostgresAgentDB

engine.AgentDB = lambda path, blob_min_bytes=4096: PostgresAgentDB(DSN, blob_min_bytes)
```

Every Part B operation then runs on Postgres. What does not yet: the places in
EconoContext that run SQL on the store directly (`db.rows(...)`, `db.execute(...)`,
`db.conn`), which are SQLite-shaped. `upstream_runs/results-jev-*.txt` lists exactly
which tests they break, and so which call sites to move onto the named operations.

## What the tests check

`tests/test_contract.py` has two parts. The first runs every behaviour the SQLite
reference implements against **both** backends, so Postgres must agree with it:
write-once segments, windows, blobs, versions and invalidation, dependents, snapshot,
outcomes, spans. The second covers the operations the contract names that the reference
only has as SQL in its callers (`learn/`, `monitor/`, `pricing/`, the gateway, `bench/`);
the expected values come from that SQL.

## Where this knowingly differs from the reference

- **Timestamps** are `timestamptz`. Operations return them as ISO-8601 strings, so they
  compare and sort like the reference's text timestamps.
- **JSON** columns are `jsonb` and come back decoded (dict / list), not as JSON text.
- **Booleans** are `boolean`, not 0/1.
- **Invalidation** is one SQL statement over `jsonb_each_text(read_set)`, not a Python loop.
- **Search** (`search_segments(run_id, terms, limit)`) ranks by the number of terms a
  segment contains, newest first among equals, one hit per distinct content. There is a
  GIN full-text index, but term matching uses `ILIKE` so paths and identifiers match
  as substrings; a trigram index (`pg_trgm`) would serve that better and is not in the
  pip Postgres build.
- `execute` and `rows` are kept as plain-SQL escape hatches for parity with the
  reference; they are not part of the contract and take `%s` placeholders.

## Operations beyond the reference, and where their meaning came from

| operation | the SQL it replaces |
|---|---|
| `get_run`, `list_runs`, `labelled_runs` | `omnigent_layer/__init__.py`, `bench/learn.py`, `learn/predictors.py` |
| `first_segment`, `segments_of`, `assistant_segments` | `monitor/context_map.py`, `learn/labels.py` |
| `search_segments` | `store/retrieval.py` |
| `tool_results_with_text`, `read_sizes`, `count_tool_results` | `learn/labels.py`, `monitor/context_map.py`, `bench/report.py` |
| `decisions_of`, `decision_summary`, `replay_rows` | `learn/labels.py`, `pricing/ledger.py`, `learn/replay.py` |
| `outcomes_of`, `outcome_totals`, `calls_by_agent`, `worker_calls`, `predicted_vs_actual`, `last_prompt_tokens`, `input_tokens_since` | `bench/report.py`, `pricing/ledger.py`, `monitor/context_map.py`, `omnigent_layer/gateway.py` |
| `spans_of`, `has_open_model_span`, `count_model_spans` | `pricing/ledger.py`, `monitor/context_map.py`, `omnigent_layer/gateway.py` |
| `replace_labels`, `result_labels` | `learn/labels.py`, `learn/predictors.py` |
| `add_gateway_pointer`, `gateway_pointers` | `omnigent_layer/gateway.py` |
