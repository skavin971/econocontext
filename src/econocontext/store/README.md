# store — versioned run state

Owned by the schema workstream.

Everything a run learns lands here: what it read, what it decided, what each
call cost. The planner is only as good as what this can tell it, so the shape of
this data is not a housekeeping concern — it is the input to the research.

## Two halves, and the split matters

**SQLite** (`schema.sql`, applied by `MemoryStore.open()`) holds small indexable
rows. **A content-addressed blob store** (`artifacts.py`) holds every actual
byte — prompts, file contents, model responses, assembled requests — keyed by
SHA-256 of the content.

A row never holds a payload. It holds a 64-character digest that points at one.
Run `econocontext schema --counts-only` and the split is visible immediately:

```
records              1,331          85,184          64      <- 64 bytes per row
artifact store   6,699 objects   24,655,404 bytes on disk
```

That is the whole design in two lines. Identical content is stored once no
matter how many runs reference it, writes are atomic (`tempfile` + `os.replace`),
and `get()` re-hashes on read, so corruption is caught rather than served.

## The tables

| Table | Holds | Notes |
|---|---|---|
| `runs` | One row per run: status, request hash, payload ref | `request_key` is the idempotency key, unique |
| `records` | Everything typed: workers, operations, plans, results, evidence, attempts | One generic bag discriminated by `kind` |
| `bindings` | `(run_id, source) -> evidence_id`: the *current* version of each source | This is what makes staleness detectable |
| `messages` | Per-worker conversation, append-only, ordered by `seq` | A worker's context is reconstructed from here |
| `events` | The run's narrative: assembly, planning, selection, representation | What `explain` and `compare` render |
| `search_text` | Full text of each evidence object, for retrieval | The one place text is duplicated out of the blob store |
| `migrations` | Exactly one row, version 1 | `open()` refuses any other value rather than guessing |

`bindings` versus `records` is the important pair. Evidence is immutable and
content-addressed, so re-reading a changed file creates a *new* evidence row;
`bindings` advances to point at it. Everything the planner knows about staleness
comes from comparing an operation's recorded `bindings` against current ones
(`MemoryStore.compatible`). Get this wrong and REPAIR and REUSE become unsound.

## Where to start

1. `econocontext compare --fixture ledger --methods react econocontext` — make
   some data.
2. `econocontext schema` — the DDL in force, then what accumulated under it.
   Pass a `run_id` to scope the counts to one run.
3. `econocontext export <run_id>` — the full trace as JSON, which is what a
   better schema should be able to answer questions about without Python.

Design v2 against what runs actually produce, not against what the tables look
like they should hold. The measurements the project needs are in
`docs/runs/*/README.md`; 

## Changing it

`schema.sql` is applied on every `open()` with `CREATE TABLE IF NOT EXISTS`, so
a fresh checkout self-creates and needs no migration step. That also means
**changing an existing table does nothing to an existing database.** Bump the
version in `migrations` and write a real migration, or delete `data/` and
re-run. `open()` will refuse a database whose version it does not recognise
rather than silently working against the wrong shape.
