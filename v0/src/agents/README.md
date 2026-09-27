# agents — what gets built on the harness

Owned by the agent workstream.

EconoContext is a harness: it decides *where* work runs, *what state* it sees
and *how* it proceeds. It carries no idea what the work is. That is here.

**The assignment is in [docs/WORKSTREAM-AGENTS.md](../../docs/WORKSTREAM-AGENTS.md)** — what to build
next and the measurements that say why. This file is how to work in the code.

## The shape

```
agents/
  __init__.py       AGENTS registry: name -> class
  base.py           staging, workspace, subprocess, refresh, read + search
  coding/
    agent.py        CodingAgent
    prompts.py      what it is told
    tools.py        what it can do beyond read and search
    fixtures/
      ledger/       GOAL.md · verify.py · task/   <- files staged into the run
      parser/
  research/         same shape, thinner
```

Fixtures are real files. `task/` is copied into the run workspace, `GOAL.md` is
the prompt when no `--prompt` is given, and `verify.py` is the hidden check. Edit
them with an editor; nothing is generated from a Python string any more.

## Adding an agent

1. `cp -r research my_agent`, rename the class, set `name` and `default_fixture`.
2. Declare tools in `tools.py`, write the system prompts in `prompts.py`.
3. Implement `execute` for your tools — call `super().execute()` for read and
   search — and `verify` for what counts as done.
4. Add it to `AGENTS` in `__init__.py`.
5. Put a fixture under `fixtures/<name>/` with `GOAL.md`, `task/`, and a
   `verify.py` that uses **different inputs from anything the agent can see**.

`BaseAgent` already gives you staging (a directory or a pinned git commit,
snapshot-copied so a failed run never touches the caller's files), workspace path
containment, bounded subprocess execution, `read`, `search`, and `refresh()`.

**`refresh()` is not optional.** After anything that changes a file, call it. The
store detects staleness by comparing source versions, and REUSE and CONTINUE are
only sound if it knows what moved. `CodingAgent.execute` calls it after every
`apply_patch` for exactly this reason.

## Verification is the measurement

Cost only means something at matched quality, so `verify` decides whether a
number is comparable at all. An arm that is cheaper because it failed is not
cheaper. Two rules:

- **Hidden checks use different inputs from the visible ones.** The ledger
  fixture's `verify.py` tests `parse_amounts(' 7 , , -3 ,')` where the visible
  test uses `'4, ,5,,'`, so special-casing what the agent can read does not pass.
- **A model saying it finished is not evidence.** Results stay `unverified`
  unless something independent confirms them.

## Measuring cost

```sh
econocontext compare --fixture ledger --methods react econocontext \
  --context-tokens 1048576 --plan-pressure 0.02 --max-cost 1.00 \
  --output docs/runs/$(date +%F)-my-experiment
```

Writes a walkthrough and a full trace per arm, plus a cost/time summary. Every
request and response goes to `logs/run-<unix>-<run_id>.txt`.

**`--plan-pressure` is load-bearing.** Delegation is only offered once the root
passes `context_tokens × plan_pressure`. Leave it at the 0.5 default with a large
budget and delegation never fires, so both arms run identically and the
comparison is a null result that looks like a finding. Check the trace for
`context_pressure` before believing any number.

Read `docs/runs/2026-09-22-no-context-cap/README.md` first. It records the case
where econocontext cost *more*, and why that is the honest result.

## What the research agent needs

It is a slot, not a research capability. It can find a passage and quote it. It
cannot:

- **take notes** — every intermediate finding has to stay in the context, which
  is exactly the pressure the harness exists to relieve
- **track a claim to a source** — `verify` currently checks that one known
  filename was cited and two numbers appear in the answer, which does not
  generalise past this one corpus
- **build a report** — there is no structure to the answer beyond a string

A real version needs a `note` tool writing to the store rather than the context,
a `cite` tool binding a claim to an evidence id and span, and a verifier that
checks each claim against the passage it names. That is the assignment, and it is
also the best test of the harness: note-taking is what makes context pressure
real, and the no-context-cap run showed the ledger fixture never gets there.
