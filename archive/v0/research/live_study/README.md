# Live study: context policies at real cost, on real tasks

Pilot results: `docs/runs/2026-09-25-live-study/report.html`. The reasoning behind
the study: `docs/research-notebook.html`.

Every number this produces comes from a real model call, priced at the platform's
list price and, where possible, reconciled against the bill. Nothing is simulated.

## What varies and what does not

| Fixed across every run | Varies |
|---|---|
| the harness, the SWE agent and its tools | **policy** P0–P3 (`policies.py`) |
| the task's container image and hidden tests | **model**: Gemini 3.6 Flash (implicit cache, 90% off); gpt-oss-120b (no cache) was tried and skipped |
| 120K context window, 60K threshold, keep-3 floor | **task**: SWE-rebench instances |
| 100 turns, 16,384 output tokens per call, per-run cost cap | |

| Policy | Rule | Stands in for |
|---|---|---|
| P0 | append-only | naive baseline |
| P1 | over 60K, summarize the middle with a billed call | OpenHands condenser, Claude Code auto-compact |
| P2 | over 60K, clear all tool outputs but the newest 3 | LangChain `ClearToolUsesEdit` |
| P3 | place each large result by forecast holding cost; clear old ones on a ski-rental rule priced by the cache; sweep when cold | EconoContext |

## Prices

Vertex AI list prices, global endpoint, per million tokens, read 2026-09-25 from
<https://cloud.google.com/vertex-ai/generative-ai/pricing> (`models.py`):

| Model | Input | Cache read | Cache write | Output |
|---|---|---|---|---|
| Gemini 3.6 Flash | 0.75 | 0.075 | — (implicit) | 3.75 |
| gpt-oss-120b | 0.09 | none listed | — | 0.36 |
| *Claude Sonnet 5 (skipped)* | 2.00 | 0.20 | 2.50 (5 min) | 10.00 |
| *Gemini 3.5 Flash (unused)* | 1.50 | 0.15 | — (implicit) | 9.00 |

Gemini 3.6 Flash's rates are promotional through 2026-12-31 (1.50 / 0.15 / 7.50
after); the pricing revision on every run records which applied.

gpt-oss-120b reports cached tokens, but Vertex lists no cache price for it, so they
are billed at the input rate. The bill decides whether that was right.

## Tasks

`select_tasks.py` chooses by a rule fixed before any run: every OpenHands reference
run took at least 50 turns, at least 3 runs and all resolved, pytest, 5–300
PASS_TO_PASS tests, a published image; smallest image first. The trajectories are
read for length and outcome only; no agent sees them. Download the inputs first:

```sh
# nebius/SWE-rebench-openhands-trajectories (2.1 GB) and nebius/SWE-rebench test split
python research/live_study/select_tasks.py --trajectories traj.parquet \
    --tasks rebench0.parquet rebench1.parquet --count 2
python research/live_study/check_verifier.py   # untouched must fail, reference must pass
```

## Running

```sh
python research/live_study/run.py --study pilot --cap 40 --per-run 5 \
    --models gemini-3.6-flash --policies P0 P1 P2 P3
```

A run starts only if the remaining budget covers a whole `--per-run`, and inside a
run no call starts whose worst case would pass it, so `--cap` cannot be crossed.
Each run appends one line to `docs/runs/<date>-live-study/<study>/ledger.jsonl`
and writes its full trace beside it; every request and response is also in
`logs/run-<unix>-<run_id>.txt`. Re-running the same command resumes. A run killed
by provider throttling is run once more; both attempts stay in the ledger and both
count toward the cap.

## Gates (Stage 0)

| Gate | Result |
|---|---|
| G1 containers | **pass.** SWE-rebench images run on the M2 under x86 emulation; a task's tests take ~1.5 s, a container starts in ~3 s. Both pilot verifiers: untouched fails, reference passes. |
| G2 models | **Gemini 3.6 Flash pass**: the throttle probe (`probe.py`: one recorded real turn, 10 times, 20 s apart) admitted 10/10 with no refusals; implicit caching reported at realistic sizes (16,071 of 18,297 tokens cached on repeat). **gpt-oss-120b skipped** after three pilot runs: Vertex returned its tool calls as raw harmony text on 34 of 45 turns (27 recovered, 7 not), and sending its reasoning back caused 400s (fixed). **Gemini 3.5 Flash not used**: Google's shared capacity refused most realistic requests. **Claude Sonnet 5 skipped** for now by choice; its backend is covered by a contract test. |
| G3 prices | **pass**, table above. |
| G4 billing | **open**: needs Cloud Billing export access for the project. |

## Known limits

- One run per cell is noisy; paired comparisons on the same task are the unit.
- Gemini 3.5 Flash is served only from the global endpoint for this project, and
  a normal agent request (~4K tokens) is refused with 429 most of the time even at
  one request per 20 seconds with nothing else running (2026-09-25), while tiny
  requests pass. That is shared capacity or a low project quota, not our traffic.
  Runs are slow, not wrong: refused calls bill nothing and do not count as turns.
- Different models solve different tasks. Cost is compared at matched outcome; a
  cheaper run that failed is not cheaper.
