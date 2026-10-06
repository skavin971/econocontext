# Does EconoContext cut an agent's cost? Results of 2026-10-06

**Answer so far: yes.** On 10 TBLite tasks run 3 times each, EconoContext cut the run cost by **12.4%** and passed 2 more runs. On the 5 longest tasks, the version that ignores the provider's cache cut cost by **22.5%** and passed every run. The samples are small; the caveats are below.

Simple page: [index.html](index.html). Every trial: [trials.csv](trials.csv).

## Setup

| | |
|---|---|
| Agent harness | Our own small agent loop (Python, `adapters/harbor/econo_agent.py`) with 4 tools: bash, read file, write file, submit |
| Model | Gemini 3.6 Flash (Google Vertex) |
| Benchmark | TBLite (Terminal-Bench Lite), 10 fixed tasks (the EconoCLM seed) |
| Runner | Harbor 0.16.1; each run in a fresh Docker container |
| Grading | Each task's own tests: pass or fail |
| Limits, same for every setup | at most 60 steps; tool output cut at 30,000 characters |
| Isolation | each run starts with an empty session database, deleted at the end; nothing carries over between runs |
| Cost | Gemini's bill for every call in the run (all calls, including retries and summary calls), from the gateway's log of the provider's own token counts. Jev's cost is reported separately |

**The three setups**

| Setup | What it does |
|---|---|
| No EconoContext | The agent keeps everything and re-sends its whole conversation on every call |
| EconoContext | Jev (a classifier) predicts what the agent still needs; a price decides what to keep, shrink or summarize. The price counts on the provider's cache |
| EconoContext, no cache guess | The same, but the price ignores the cache: we cannot see or control a closed model's cache, so we don't plan around it |

## Main results

| Test | Setup | Passed | Total cost | Change |
|---|---|---|---|---|
| All 10 tasks, 3 runs each | No EconoContext | 24/30 | $11.17 | |
| | EconoContext | **26/30** | **$9.79** | **−12.4%** (saved $1.38) |
| 5 longest tasks, 2 runs each | No EconoContext | 8/10 | $5.97 | |
| | EconoContext, no cache guess | **10/10** | **$4.63** | **−22.5%** (saved $1.34) |

- **Is it luck?** If EconoContext changed nothing, a saving this large would appear by chance about 5% of the time in the first test (p = 0.049) and about 1% in the second (p = 0.011), by a paired t-test. A sign test, which assumes less, gives p = 0.099 and 0.109: EconoContext was cheaper in 20 of 30 and 8 of 10 pairs.
- **No cache guess vs cache guess**, on the same 5 tasks and runs: 11.8% cheaper, not significant yet (p = 0.15).
- **Jev's own cost** (separate, at an unverified $0.042 per million tokens): about $0.10 in the first test (2.48M tokens) and $0.07 in the second (1.72M tokens).

## Per task

**All 10 tasks: average cost per run (passed)**

| Task | No EconoContext | EconoContext | Change |
|---|---|---|---|
| ACL permissions (`acl-permissions-inheritance`) | $0.066 (3/3) | $0.058 (3/3) | −12.7% |
| Anomaly ranking (`anomaly-detection-ranking`) | $0.154 (3/3) | $0.156 (3/3) | +0.8% |
| API permissions (`api-endpoint-permission-canonicalizer`) | $0.488 (3/3) | $0.456 (3/3) | −6.5% |
| Bandit algorithm (`bandit-delayed-feedback`) | $0.712 (0/3) | $0.663 (0/3) | −6.9% |
| Chained forensics (`chained-forensic-extraction`) | $0.140 (3/3) | $0.139 (3/3) | −0.8% |
| Malicious package (`malicious-package-forensics`) | $0.468 (3/3) | $0.579 (2/3) | +23.7% |
| Maven conflict (`maven-slf4j-conflict`) | $0.502 (3/3) | $0.284 (3/3) | −43.4% |
| Pandas ETL (`pandas-etl`) | $0.072 (3/3) | $0.083 (3/3) | +14.9% |
| Sales data CSV (`sales-data-csv-analysis`) | $0.403 (0/3) | $0.275 (3/3) | −31.7% |
| Linux persistence scan (`scan-linux-persistence-artifacts`) | $0.717 (3/3) | $0.570 (3/3) | −20.5% |
| **Total** | **$3.72** | **$3.26** | **−12.4%** (cheaper on 7 of 10) |

**5 longest tasks: average cost per run (passed)**

| Task | No EconoContext | EconoContext | EconoContext, no cache guess |
|---|---|---|---|
| API permissions | $0.49 (3/3) | $0.46 (3/3) | $0.39 (2/2) |
| Bandit algorithm | $0.71 (0/3) | $0.66 (0/3) | $0.68 (**2/2**) |
| Malicious package | $0.47 (3/3) | $0.58 (2/3) | $0.41 (2/2) |
| Maven conflict | $0.50 (3/3) | $0.28 (3/3) | $0.26 (2/2) |
| Linux persistence scan | $0.72 (3/3) | $0.57 (3/3) | $0.58 (2/2) |
| **Total** | **$2.89** | **$2.55 (−11.6%)** | **$2.31 (−19.8%)** |

These averages use all runs of each setup (3 or 2), so the change here (−19.8%) differs slightly from the same-run pairs above (−22.5%).

## Where the saving comes from

| All 10 tasks, 30 runs each | No EconoContext | EconoContext | Change |
|---|---|---|---|
| Input tokens sent | 27.8M | 22.2M | **−20.2%** |
| Share of input served from Gemini's cache | 69.8% | 68.8% | about the same |
| Output tokens written | 0.91M | 0.92M | +1.0% |
| Model calls | 820 | 896 | +9% |

EconoContext saves by **sending less**, not by making the model write less. In repeat 1 we priced each EconoContext run as if our edits had not happened: the edits removed $0.95 of re-sent tokens and added $0.06 of summary calls, a net **20.4%** of what those runs would otherwise have cost. Most of it came from the three tasks where a compaction happened (maven alone: $0.48).

**What EconoContext did, all counted runs**

| Action | When it fires | EconoContext (30 runs) | No cache guess (10 runs) |
|---|---|---|---|
| Big tool output arrives | keep it whole, keep a slice Jev picks, or keep a short preview; the full copy is saved in the session database | 28 whole, 58 slices, 20 previews | 19 whole, 42 slices, 16 previews |
| Compaction | Jev says a part of the work is done (≥ 0.7), the prompt is ≥ 30k tokens, and ≥ 8 calls since the last one; the model writes the summary | 7 | 2 |
| Drop finished big items | Jev says an item is done and the price says dropping it is cheaper | 0 (kept 32 times: the cache made keeping look cheaper) | **6** (kept 15) |
| Don't repeat / serve a stored copy | the same read-only call again with nothing written since | 2 notes, 1 served | 1 served |
| Mark stored copies stale | after any write | bookkeeping, every write | bookkeeping, every write |

Ignoring the cache is what let the dropping rule act at all.

## How we checked the numbers

`benchmarks/tblite/verify_gemini.py` rebuilt every number from raw sources; all 70 counted runs passed every check ([runs/2026-10-06-gemini/VERIFY-final.md](../../../runs/2026-10-06-gemini/VERIFY-final.md)):

- every gateway call re-priced from the provider's raw usage with the Gemini price card (input $0.75/M, cached $0.075/M, output $3.75/M, reasoning billed as output);
- the gateway's call count equals the agent's own call log; token totals match;
- each pass/fail equals the grader's own `reward.txt`;
- Jev's tokens in the session database equal the run summary;
- the one failed check belongs to the crashed run that is not counted: its last call was cut off, so about $0.035 of it is missing from the spend;
- p-values computed in plain Python, checked against known table values.

**Isolation**, checked on repeat 1 ([isolation-check.out](../../../runs/2026-10-06-gemini/isolation-check.out)): each EconoContext run had its own session database, filled only by that run (10 files for 10 runs), and its own fresh container. EconoContext runs got **zero** cached tokens on their first 4 calls, so no run borrowed another run's cache.

**Not counted** (10 runs, never graded): one run without EconoContext crashed in our harness (a reply with no message; fixed and rerun), and 9 runs of the no-cache-guess setup were stopped by our own gateway's daily token cap (raised and rerun). Their reruns are counted instead.

## What we learned about Gemini's cache

- About 69–70% of input tokens were served from cache in every setup.
- A call reused on average 66–70% of the previous prompt from cache; 14–20% of calls got no cache at all.
- The newest part of the prompt (median about 3,000 tokens) was usually not cached on the next call; cached counts follow no block size.
- We cannot see or control this cache, so for closed models v2.1 does not plan around it. Detecting cache breaks is an open question for the group.

## Earlier on 2026-10-05/06: Claude Code + Sonnet 5 (paused)

The first track ran EconoContext inside Claude Code through its hooks ([runs/2026-10-05-v2/RESULTS-1b.md](../../../runs/2026-10-05-v2/RESULTS-1b.md), [runs/2026-10-05-v2-spike/RESULTS-1a.md](../../../runs/2026-10-05-v2-spike/RESULTS-1a.md)).

| | Result |
|---|---|
| Hook mechanisms | rewriting a tool call, replacing a tool's output, and compacting through the SDK all work |
| Forced test of all rules | 7 of 9 worked end to end (compaction in the maven run); dropping finished items and sending work to an idle worker never came up. Harbor's forced background workers had to be turned off for worker reports to arrive |
| Maven conflict, one pair | −5% with output slices only; +23% with one compaction, because Claude Code's own `/compact` call cost $0.20 (16.8k output tokens); −11% without that call |
| Bandit, no EconoContext | failed, $0.563 |
| Why paused | Claude Code already skips identical re-reads, moves outputs over 30k characters to files and compacts; single runs (about $0.60, 40 minutes) cannot show a 10% effect |

## Caveats and open questions

- Small samples: 30 pairs and 10 pairs.
- The no-cache-guess setup ran only on the 5 longest tasks, and the no-EconoContext runs it is compared with were done earlier the same day, not side by side.
- Malicious package cost **more** with EconoContext (+23.7%, and 1 failure); not yet analysed.
- Pass-rate differences (sales data 3/3 vs 0/3, bandit 2/2 vs 0/3) are hints, not findings.
- Jev's price is unverified, and Jev accepts only about 30k input tokens, so its view of long runs is cut to 28k.
- Worker rules (send a sub-question to the worker that already holds the context) are not in this harness.

## Spend

| | Spent |
|---|---|
| Gemini, everything on 2026-10-06 (test run, rule tests, all experiments, stopped runs) | $26.63 of a $35 ceiling |
| Sonnet 5, Claude Code track | $3.85 of $7 |
| Jev | about $0.18 (separate) |

## Where everything is

| What | Where |
|---|---|
| Every trial (setup, task, pass, cost, tokens, Jev tokens) | [trials.csv](trials.csv) |
| Every model call (raw usage) | [runs/2026-10-06-gemini/gateway_outcomes.csv](../../../runs/2026-10-06-gemini/gateway_outcomes.csv) |
| Verification report | [runs/2026-10-06-gemini/VERIFY-final.md](../../../runs/2026-10-06-gemini/VERIFY-final.md) |
| Each run: Harbor logs, conversation, session database | `runs/gx1`, `gx1b`, `gx23` (main test), `gx4`, `gx4b` (no cache guess) |
| Step logs with exit codes | [runs/2026-10-06-gemini/](../../../runs/2026-10-06-gemini/) |
| Rule tests on our harness | [runs/2026-10-06-gemini/RESULTS-spike.md](../../../runs/2026-10-06-gemini/RESULTS-spike.md) |
| Code | harness `adapters/harbor/econo_agent.py`; EconoContext `econocontext/owner.py`, `pricing/lifecycle.py`, `predictor/jev.py`; runner `benchmarks/tblite/run_gemini.py` |
