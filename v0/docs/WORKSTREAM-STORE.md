# Workstream: the memory store

## Where we are

The store is not a log of what happened. It is **the planner's input** — every
decision the system makes is a query against this data, so its shape sets the
ceiling on how good the planning can get.

What exists works at fixture scale and was not designed for the job. Evidence is
immutable and content-addressed, with a separate table tracking which version is
current per source; every staleness judgement in the system is a comparison
between those two. Workers carry what they have seen and a little observed cache
behaviour. Everything else — six different record types — shares one untyped
table with the structure hidden inside a JSON blob.

## What we need

- **A schema designed for evidence versioning**, rather than one that happens to
  support it. Version identity, what superseded what, and what a given worker
  saw at the time are the load-bearing facts, and getting them wrong makes reuse
  unsound rather than slow.

- **Worker history as a first-class thing** — what each worker has seen,
  produced, what its context cost, and what it observed about cache behaviour.
  Today this is scattered and dies with the run.

- **Somewhere to express alternative representations of the same evidence.**
  Full text, a compression, a summary and a pointer are the same object at
  different fidelities and prices. There is currently nowhere to put that.

- **Recovery cost as recorded data.** The whole argument is *is keeping this
  cheaper than getting it back* — and nothing in the schema says what getting it
  back would take.

- **Retrieval that survives past fixture scale.** What we have is an unindexed
  scan and it will not hold up on a real repository.

- **A view of the data that answers research questions directly.** Several
  numbers in our last write-up needed a Python script to extract. Every one of
  those is a schema requirement.

## Where things are

`src/econocontext/store/README.md` for how the code works and where the tables
are. `econocontext schema` prints the DDL in force and what a real database
accumulated under it — worth running against a few real runs before proposing
anything.
