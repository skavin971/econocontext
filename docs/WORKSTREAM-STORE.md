# Workstream: the memory store

What to build and why. `src/econocontext/store/README.md` is the how-to for the
code; this is the assignment.

**The framing that matters:** the store is not a log of what happened. It is
**the planner's input**. Every decision the system makes is a query against
this data, so the shape of the schema sets the ceiling on how good the planning
can get. Design it backwards from what the planner needs to ask.

## What the planner actually reads

One call does almost all of it — `MemoryStore.find_candidates()` returns:

| Key | What it is | Used for |
|---|---|---|
| `required` | evidence the operation named explicitly | feasibility |
| `direct` | top lexical matches for the goal | the FOCUSED view |
| `related` | bounded neighbours | the BROADER view |
| `evidence` | the objects themselves | sizing every candidate |
| `workers` | live workers in this run | CONTINUE candidates |
| `results` | prior results under this operation key | REUSE candidates |
| `revision` | digest of current bindings | staleness detection |

### The three records everything rests on

**`Worker`** — `bindings`, `fingerprint`, `scope`, `status`, `revision`,
`context_manifest`, `cache_observation`.

`bindings` is the important one: which evidence versions this worker has seen.
A worker is eligible to continue only if every binding still matches the current
version.

**`Evidence`** — `source`, `version`, `payload`, `kind`, `excerpt`, `tokens`,
`description`. Immutable and content-addressed: `version` is the SHA-256 of the
content, `payload` is the key into the artifact store.

**`Result`** — `operation_key`, `requirements`, `fingerprint`, `verification`,
`reusable`. REUSE is offered only when the key matches, every requirement's
version still matches, the fingerprint matches, and verification is not
`failed`.

### Why `bindings` vs `records` is the pair to understand first

Evidence never changes. Re-reading a modified file creates a **new** evidence
row; the `bindings` table advances its pointer to it. So:

- `records` = every version that ever existed
- `bindings` = which version is current, per source

Every staleness judgement in the system is a comparison between those two
(`MemoryStore.compatible`). **Get this wrong and REUSE and CONTINUE become
unsound** — the planner would hand back answers about files that have since
changed. This is the invariant to protect above all others.

## What is missing, and what each gap blocks

Every item here is something the vision page claims and the schema cannot
currently express.

### 1 · Representations

The page describes evidence materialising as FULL, COMPRESSED, STRUCTURED or
POINTER, each with a different token cost and fidelity. **There is no field for
this.** `Evidence` has one `payload` and one `tokens` count.

Needs: alternative materializations of one logical object, each with its own
size, its own artifact reference, and a minimum fidelity below which it may not
degrade. Blocks the entire "what it sees" axis.

### 2 · Recovery cost and expected future use

The page's whole argument is *is keeping this cheaper than getting it back?* —
and **nothing in the schema records what getting it back would cost.** No
recovery route, no price, no probability of being wanted again.

Today the cost model infers it from profiles keyed by operation shape. That
works for planning a call; it cannot answer "what is this object worth keeping".
Blocks any real eviction or compaction decision.

### 3 · A holder registry

Cross-agent asking — the cheapest copy being in another worker's warm
context — needs an index of **which workers hold which evidence, at which
version, in which representation**.

The data half-exists: `Worker.bindings` says what a worker has seen. But it is
per-worker, so answering "who holds this file?" means scanning every worker.
That is fine at five workers and wrong at fifty. Blocks the whole "where it
runs" mechanism.

### 4 · Cache observations that outlive a worker

`Worker.cache_observation` records what a worker saw. The estimator wants
observed cache fractions grouped by **prefix fingerprint, across runs**, so `f`
can be predicted *before* the worker exists.

Right now the prediction needs at least three matching samples or it credits
zero. If the samples die with their workers, `f` stays zero forever and the
cache term never learns anything.

### 5 · Retention

Nothing is ever deleted. The artifact store grows without bound across runs, and
blobs are shared between runs by content hash — so safe deletion needs
refcounting or a mark-and-sweep against every referencing row. 24 MB after
twenty runs; this becomes real quickly.

## Known weak points in what exists

- **Retrieval is an unindexed LIKE scan** (`memory.py:224`). Candidate evidence
  is ranked by summing `text LIKE ?` over up to twelve extracted words across
  every row for the run, capped at 24 results. It avoided an FTS5 dependency and
  it works at fixture scale. It is O(rows × words), it cannot rank beyond
  counting substring hits, and it is the first thing that falls over on a real
  repository. FTS5 is the obvious answer — measure before and after.
- **`records` is one untyped table for six kinds.** Workers, operations, plans,
  results, evidence and attempts all share `(id, run_id, kind, lookup, payload)`
  with the structure inside a JSON blob. Nothing is queryable by field. You
  cannot ask "which plans overestimated cost" in SQL — `telemetry.metrics()`
  loads everything and does it in Python. Typed tables, or generated columns
  over the JSON, would make the research questions answerable directly.
- **Text is stored twice.** Evidence text lives as an artifact *and* as a
  `search_text` row, because SQLite cannot search the blob store.

## Where to start

1. `econocontext compare --fixture ledger --methods react econocontext` — make
   some data.
2. `econocontext schema` — the DDL in force, then what accumulated under it.
   Pass a `run_id` to scope the counts to one run.
3. `econocontext export <run_id>` — the full trace as JSON.

**Design v2 against what runs actually produce, not against what the tables look
like they should hold.** The specific test: read
`docs/runs/2026-09-22-no-context-cap/README.md`. Several numbers in it took a
Python script to extract. **Every one of those is a schema requirement** — if
answering a question about a run needs a script, the schema is not carrying its
weight.

## Changing the schema safely

`schema.sql` is applied on every `open()` with `CREATE TABLE IF NOT EXISTS`, so
a fresh checkout self-creates and needs no migration step. That also means
**altering an existing table does nothing to an existing database.** Bump the
version in `migrations` and write a real migration, or delete `data/` and re-run.
`open()` refuses a database whose version it does not recognise rather than
silently working against the wrong shape.
