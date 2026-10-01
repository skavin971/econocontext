# The cost model explains FRESH vs RESUME (offline, $0)

Date: 2026-09-30. Plan step 1 of "price = meter(forecast)".
Reproduce with `.venv/bin/python harness/explain_placement.py`.

## What changed

- **`econocontext/costmodel/meter.py`**: the meter.
  - `price_call` is the one per-call price formula; `pricing/ledger.py` now uses it.
  - `Loop` is a loop of N append-only calls, with a closed form.
  - `ExplicitCache` models Anthropic's cache: the previous prompt is read and the new part is written.
  - `ImplicitCache` models Gemini's: the previous prompt minus a lag is read, unless the call misses the cache.
- **`econocontext/costmodel/options.py`**: FRESH, RESUME and HANDOFF as loops. It also gives two break-evens: how many calls an option must save, and COMMIT_PENDING's N*.
- **`econocontext/costmodel/bash_reads.py`**: shell commands that only read become line reads.
  - It handles `sed -n`, `cat`, `nl`, `head`, `tail`, `awk NR`, `grep -n` and `rg -n` (their output lines), and `| head`.
  - It follows `cd` and `F=…; $F`, and unwraps `docker exec … bash -lc` and `bash -c`.
  - Anything else counts as not read-only.
- **Evidence (`omnigent_layer/observe.py`)**:
  - Every file read is held as `L<a>-<b>` of a version.
  - A read-only Bash or `run_shell_command` call acquires lines instead of counting as a change to every file.
  - Claude Code's Read results are numbered (`634\t…`), so their lines come from the text.
- **Worker holdings** (`claude_workers.holdings`): file → [(first line, last line, version)].
- **Placement** (`planner.for_placement`) is priced by the meter.
  - Shape per harness: `cost_model.worker_shapes`. Cache per provider: Anthropic is explicit, Gemini implicit.
  - Only warm workers can be resumed.
  - `placement.force` pins an experiment arm.

## Results

| Check | Result | Gate |
|---|---|---|
| Meter vs the recorded ledger | 440 priced calls; largest difference 1.2e-16 | exact: **pass** |
| Explicit cache split (Claude Code), predicted from prompt sizes alone | 10 runs: total −0.23%, per run ≤ 0.48% | ≤ 2%: **pass** |
| Implicit cache split (Gemini): miss 0.19, lag 2,539, fitted on 243 call pairs | 16 runs: total +1.88%, per run median 10.4% (max 26.8%) | ≤ 5% over all runs: **pass**, see the caveat |
| wctl4 follow-up, FRESH, model at the observed N=5 | 49,434 vs 49,845 NU billed (−0.8%) | ≤ 5%: **pass** |
| wres4 follow-up, RESUME, model at the observed N=9 | 85,111 vs 82,314 NU billed (+3.4%) | ≤ 5%: **pass**, ranked correctly |

**Before the decision**, both options get the same forecast: N = 10.5, the median of the phase-1 worker loops.
- **The new model:** FRESH 79,754 NU, RESUME 101,746 NU, so it chooses **FRESH**, which was right.
- **What RESUME would have needed:** about 2.1 fewer calls than a new worker. It took 4 more.
- **The first model:** it logged RESUME at 25K and FRESH at 55K, and chose RESUME. Its errors:
  - FRESH was charged 8,950 uncached tokens per call, but a new Explore worker finds an 8,011-token prefix already cached.
  - Both options were assumed to take 5 calls.

**Why resuming saved nothing: line overlap, with Bash reads counted.**
- **wres4:** of the 800 lines the resumed worker read for the follow-up, it already held **8%**.
- **wctl4:** of the 836 lines the new worker read, the idle worker held **21%**.

The follow-up needed different code, so RESUME's history was paid on every call and saved no reads.

**COMMIT_PENDING in c4, re-derived.**
- **The trade:** the pointer saved 878 tokens, but forced the 1,227 tokens after it to be sent again.
- **Break-even:** N* = 1 + (1.25·349 − 0.1·1227)/(0.1·878) = **4.6 calls**.
- **With the 0.3 chance of a re-read** (one more call, about 5.4K NU): about 23 calls.
- **Calls left:** about 14, so AS_IS was right. This matches what was logged.

## Measured worker shape (Claude Code, Explore)

| Field | Value |
|---|---|
| Shared cached prefix | 8,011 |
| First-call tail | 3,520 + the task |
| Growth per call | 2,073 |
| Output per call | 567 |
| Resumed first-call tail | 2,900 + the task |

These values are in `config/econocontext.yaml`. The default shape, used for Gemini and openai-agents workers, is still a placeholder: only two worker loops have been recorded there (plan step 6).

## Caveats

- **Sample size:** n = 1 pair for placement, with in-sample fits. The Claude Code shape comes from two phase-1 loops.
- **Gemini's misses are random.** A single run can be 10–27% off its expected cost, so the implicit model is judged over many runs.
- **Bash coverage:** a script that greps several different files and prints bare line numbers cannot be attributed to a file. Those lines are skipped (1 of 39 commands here).
- **What's still missing is a forecast of the call count.** Until one exists, placement gives every option the same count, so RESUME and HANDOFF can only win if their first prompt is smaller. That is the job of E4: forced pairs on Gemini, step 6.
