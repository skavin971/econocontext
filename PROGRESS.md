# Progress and handoff (2026-10-11)

This file is for whoever continues the work, person or agent, for example a GitHub Claude agent. It lists:
1. what the project is;
2. how the work is run, and the rules;
3. how to run things;
4. what is done;
5. where step 11 stands, with the exact remaining work.

The full record of decisions is `docs/tier-a-decisions.md`. Each step's logs and reports are in `runs/tier-a/`.

**Branch:** `tier-a-purdue` at <https://github.com/skavin971/econocontext>.

**Tags:**
- `econo-jev-final` (`912d7d1`): everything from before Tier A, i.e. v0, the v1 engine, Omnigent, the Claude Code hooks and the Gemini runner.
- `tier-a-step9` (`a4b4a2c`): the last commit before the step-10 clean-up.

**State (2026-10-13): step 14b (re-reads, versions and CLM's management cost; offline) is done, waiting for Kavin's approval.**
- The report is `runs/tier-a/step14b-report.md` (tables in `runs/tier-a/step14b/summary.md`); 14a's is `runs/tier-a/step14a-report.md`, step 13's `runs/tier-a/step13-report.md`.
- Pushed: `edad515`, `61b6cf7`, `cb04112`, `9d3f757` (14a). Unpushed: the 14b commit.
- BrowseComp-Plus data, trials and gateway logs live in `~/econo/bcp/`, never in the repo.
- To run BrowseComp-Plus: start the gateway with `--max-tokens-for bcp=4096 --log-dir-for bcp=~/econo/bcp/gateway --max-calls-per-run 300`, and `benchmarks/bcp/search_server.py`; then `benchmarks/bcp/run.py` and `judge.py`.
- Step 12g was pushed (`ace1995`).
- Steps 12d and 12f were pushed (`401773a`, `6a7bc60`); their reports are `runs/tier-a/step12d-report.md` and `step12f-report.md`.
- Step 12c was approved and pushed (`a2613d7`); its report is `runs/tier-a/step12c-report.md`.
- Step 12b (analysis and three Owner fixes) was approved and pushed (`0a1ffd4`); its report is `runs/tier-a/step12b-report.md`.
- Step 12a (the agent DB) was approved and pushed (`6c1eb7a`).
- Tests: 144 pass (`.venv/bin/python -m pytest -q`, which also runs `AgentDB/tests`).
- Setup for EconoContext runs: `pip install -e ".[dev,measure,agentdb]" -e AgentDB`, `docker compose up -d`, and `AGENTDB_DSN` in `.env`.
- Step 11: done. The interface is frozen at `47ccfdc`.

Section 5 below is kept as the record of how step 11 was specified.

---

## 1. What this is

EconoContext is online context optimization for LLM agents.
- Between an agent's steps, a predictor answers questions about the agent's context. The predictor is Jev (TypeSafe's classifier) or fixed guesses.
- A price then decides what to keep, shrink, evict or summarize.

**Tier A** (this branch, since 2026-10-10) measures this on an open model, against CLM's baselines.
- **The model:** `qwen3.8:27b` on Purdue GenAI Studio. That is Qwen/Qwen3.8-27B-FP8 at revision 017b9c7, served by vLLM 0.30.0 with a 65,536-token window and a limit of 20 requests a minute.
- **The cost measure:** prefix-reuse FLOPs, following CLM, *Context Language Models* (arXiv 2609.37725), Appendix C, Eq. 9.
- **The prompts:** every prompt is rebuilt from the gateway's call log and validated token for token against the server's own prompt ids.

**The comparison follows CLM's design (step 10):**
- every method runs zero-shot, on the same model and settings;
- every arm has a 32,000-token budget;
- every arm uses CLM's sampling: temperature 0.7, top_p 0.95, thinking on, reasoning_effort medium, and at most 2,048 output tokens per call.

**The arms:**
- Base: no context management, plus CLM's budget rule;
- Summary: Codex-style compaction at 75% of the budget;
- CLM's own agent;
- EconoContext, whose budget behaviour is still to be designed together with Kavin.

## 2. How the work is run (Kavin's rules; please keep them)

- **Steps.** Work goes in steps (10, 10a, 10b, …). After each one: commit locally, report, and **STOP for approval**, unless the step says to continue. When something is unclear or unexpected, **stop and ask; don't guess**.
- **Pushing.** Push only after approval. The last thing before every push is `.venv/bin/python scripts/check_secrets.py --range econo-jev-final..HEAD`; abort on any hit. It checks for any `.env` value and for Purdue's access-grant fields (`access_control`, `group_ids`, `user_ids`, `user_id` as JSON keys).
- **Secrets.** API keys live only in `.env`, which is git-ignored: `GENAI_API_KEY` for Purdue and `TYPESAFE_API_KEY` for Jev. Never print, log or commit a key. **Only the gateway reads model keys.** Purdue's internal metadata (UUIDs, access grants) must never be published.
- **CLM's code is used, never patched or copied.** It is a pinned submodule, `baselines/clm/upstream`, at `18dc111`, under CC BY-NC 4.0.
  - The one exception is CLM's final-turn notice, used verbatim with attribution in `harness/prompts/clm_final_turn.txt`, because its wording affects behaviour.
  - Codex's compaction prompt and summary prefix (Apache-2.0) are in `baselines/summary/`, with `LICENSE-codex` and `NOTICE-codex`.
- **Research, not engineering.** No one-off patches: failures are findings, and only principled fixes are made, announced in the report.
- **Logs.** Log every step's commands and exit codes into `runs/tier-a/step<N>-*.out`.
- **Rate limits.** Purdue allows 20 requests a minute. Run one task per live run, and never call the API from tests.
- **Docker.** Run trials with `--workers 1`, or build the image once first. In tc9, two parallel workers building the same image failed with BuildKit "lease does not exist" errors.
- **Not to commit:** `docs/html/` and `notes/` are Kavin's untracked files; leave them untouched.

## 3. How to run things

**Environments:**
- `.venv` (Python 3.12): `pip install -e ".[dev,measure]"`. It holds Harbor 0.16.1, openai and transformers (for the tokenizer and template only).
- `.venv-clm` (Python 3.12): `pip install -e baselines/clm/upstream`, plus transformers and CPU torch. It runs CLM's agent and the CLM cross-checks.

**The tasks:** OpenThoughts-TBLite, outside the repo, at `~/econo/OpenThoughts-TBLite`; `TBLITE_DIR` overrides the location. The 10 frozen tasks are listed in `benchmarks/tblite/tasks.py`.

**The gateway**, which must run first, in the background:
```
.venv/bin/python -m gateway.server --provider purdue >> runs/tier-a/<step>-gateway.out 2>&1
curl -s http://127.0.0.1:8787/health
```
- It pins temperature 0.7, top_p 0.95, max_tokens 2,048 (`--max-tokens` changes it), `chat_template_kwargs.enable_thinking` true, reasoning_effort medium and `return_token_ids`. Every change it makes is listed in the log's `edits`.
- It strips earlier reasoning from assistant messages.
- It logs every call to `runs/gateway/<run_id>/calls.jsonl`.
- Its caps are 150 calls per run and 8,000 requests per day.

**A run**, after step 11 (the old `--arm` became `--policy`):
```
.venv/bin/python benchmarks/tblite/run.py --label <label> --policy base,summary,clm --budget 32000 \
  --tasks bandit-delayed-feedback --repeats 1 --model qwen3.8:27b --workers 1 --gates \
  --progress runs/tier-a/<step>-progress.md [--interface ours|legacy]
```
- **Policies:**
  - `base`: no context management, i.e. CLM's Base with a budget;
  - `summary`: Codex compaction at 75% of the budget; needs a budget;
  - `econo+jev` and `econo+prior`: refuse a budget;
  - `directed`: a stub that refuses to run;
  - `clm`: CLM's own agent through `baselines/clm/run.sh`; needs a budget.
- **Arm names:** with a budget, an arm is named `<policy>-<N/1000>k`, e.g. `base-32k`.
- **Gates** stop the run on: 3 or more invalid calls in a trial; an arm crashing twice in a row; Jev falling back on more than half its questions; a Summary compaction whose summary hit the output cap.
- **While running**, watch the trial's `calls.jsonl`. No new call for 10 minutes means a stall, for example a sleeping Mac.

**Measuring:**
```
.venv/bin/python -m measure.report runs/<label> --out runs/<label>    # report.csv, calls.csv, arms.csv
.venv/bin/python -m measure.profile runs/<label> --out runs/<label>
.venv-clm/bin/python tests/measure/clm_crosscheck.py --from-calls runs/gateway/<label>.clm-*/calls.jsonl
```
Scripts for the step-10 smoke and diagnosis, which can be adapted to a new label, are in `runs/tier-a/scripts/`.

**Tests:** `.venv/bin/python -m pytest -q`. No test calls a model.

## 4. Done so far (details in `docs/tier-a-decisions.md`)

**Steps 0–8:**
- the Purdue setup;
- the gateway;
- `measure/` (Eq. 7 constants; a 16-token prefix cache with vLLM's last-token rule; Eq. 9; token-by-token validation in which a key reorder is accepted only inside the tools block);
- the CLM cross-checks, with prefill exactly equal to CLM's own code;
- the Jev budget fix.

**Step 9: tc9, the pre-alignment baseline.** It ran raw, econo+jev, clm-32k and clm-full on the 10 frozen tasks, 3 repeats each, with each agent's own sampling, max_tokens 8,192 and CLM's 120 s command timeout.

| arm | passes / 30 | total PFLOPs |
|---|---|---|
| raw | 25 | 63.1 |
| econo+jev | 27 | 60.1 |
| clm-32k | 29 | 45.5 |
| clm-full | 26 | 49.2 |

It is to be re-run under the aligned settings after step 10. See `docs/results/2026-10-11-tblite-arms/`.

**Step 10: CLM's design for every arm.**
- **10.0:** the clean-up (`docs/cleanup-map.md`). CLM moved to `baselines/clm/upstream`.
- **10a:** the push gate's access-grant check; tc9 completed by rerunning 3 trials; Jev at 2.3 characters per token; CLM's command timeout at 180 s.
- **10b:** CLM's sampling and the 2,048 cap pinned in the gateway.
  - Live check: our rebuild equals the server's prompt.
  - Template probes: `enable_thinking` reaches the template.
  - `top_p` reaches the sampler (probes 10–13).
  - vLLM 0.30 reports a reply cut inside a tool call as `tool_calls`, and drops the cut parameter. We keep plain vLLM behaviour and count calls that hit the cap.
- **10c:** the budget rule, following CLM's released `budget.py`. Over budget − 2,048 the model gets ONE final turn with CLM's notice; still over at the next check, the run ends.
  - Every ending is graded as the container stands.
  - `measure/tokens.py` has `count_prompt`, which matches the server's count exactly on 1,432 of 1,875 tc9 calls and is +1 to +3 tokens on the rest (tool-schema key reordering), never lower.
  - EconoContext refuses a budget.
- **10d:** the Summary baseline, Codex 0.146.0 compaction.
  - It triggers at 75% of the budget (24,000 at 32k), sends no tools in the compaction call, and rebuilds the conversation as `[system, task, "{SUMMARY_PREFIX}\n{summary}"]`.
  - Smoke run `tc10s`: all 141 calls validated.
    - acl: base, summary and clm all passed.
    - bandit: all three failed.
    - On bandit, calls at the cap: raw 23 of 45, summary 30 of 65, clm 1 of 16.
- **10e:** a diagnosis, with no changes.
  - CLM's release has no Base or Summary harness.
  - Our agent hits the 2,048 cap mainly through a loop: after a reply cut while thinking, our nudge leads the model to think to the cap again. 16 of raw's 23 cuts and 17 of summary's 30 came right after a nudge. CLM recovers in one reply.
  - At 8,192 (tc9 bandit), 18% of our calls after a tool result thought past 2,048 tokens, against 7% of CLM's.
  - The summary in 10d was 1,933 tokens: 32 of thinking and 1,901 visible.

**Not ours:** `AgentDB/` (commit `401f1da`) is Kavin's own addition.

## 5. Step 11: our own harness, with the baselines separated (done; see the state at the top)

### Kavin's spec (verbatim)

> Push 7221d33 behind the gate. Then step 11: our own harness, with baselines separated. One validation run only. Show the map before moving files, then continue without stopping unless something is unclear.
>
> Layout.
> harness/ (from agents/react/): the loop, interface, tools, budget gate and logging.
> econocontext/policy.py: the econo policy wrapping Owner, with no rule changes. Add a stub directed that refuses to run.
> baselines/base/ and baselines/summary/: the 10c and 10d behaviour, unchanged. The Codex files move with Summary.
> baselines/clm/: unchanged.
> --policy chooses which one runs.
> Import-isolation test: harness/ imports neither econocontext/ nor baselines/, and econocontext/ never imports baselines/.
> Keep --interface legacy for reproducing old runs.
> Our interface, written by us.
> Exactly one tool call per response; reject extras with a short message.
> A nudge that says the last turn had no tool call (or was cut off) and asks for the next single action.
> A short system prompt asking for small steps, and to act rather than plan at length.
> Tools stay bash, read_file, write_file and submit.
> Every policy sends the identical prompt and tools (add a test). No context-management text in the shared interface.
> One validation run, 1 repeat each, at --budget 32000:
> base on bandit and scan-linux;
> summary on bandit.
> Report each next to clm-32k:
> share of calls at the 2,048 cap;
> cuts right after a nudge;
> steps used, pass and end reason;
> PFLOPs and validation.
> If Base's cap share is under about 15%, freeze the interface: record the commit in docs/tier-a-decisions.md. If it's higher, stop and report; don't iterate.
> Budget unit, from existing logs only (no new runs). Over the tc9 and 10d CLM calls, report CLM's tiktoken count against the exact Qwen prompt:
> the median ratio;
> CLM's real Qwen peak at its 32k setting;
> if that peak is above 29,952, the context_budget_tokens value that matches our limit.
> Tests pass, commit, then stop with the report.

### Done (in this commit)

- **Push:** 7221d33 was pushed behind the gate.
- **The map:** written and shown, in `docs/harness-map.md`.
- **Moves (`git mv`):**
  - `agents/react/agent.py` → `harness/agent.py`;
  - CLM's notice → `harness/prompts/clm_final_turn.txt`;
  - the Codex files → `baselines/summary/`;
  - `tests/agents/` → `tests/harness/`.

  `agents/` is removed.
- **New code:**
  - **`harness/interface.py`:**
    - `TOOLS`, unchanged;
    - `Interface` objects `OURS`, the new default with our own texts, and `LEGACY`, the step 9–10 texts and behaviour for reproducing old runs;
    - `stored_text()`, which loads a stored prompt byte for byte from after its `# ---` header line.

    Under OURS, only a reply's first tool call runs; extra calls get a short "Not run" note. A reply that hit the output limit has its calls not run, and it gets a note asking for the next single action. The nudge says whether the last reply made no tool call or was cut off.
  - **`harness/tools.py`:** bash, read_file and write_file run in the container, plus the output cap.
  - **`harness/budget.py`:** `BudgetGate`, CLM's rule, unchanged from 10c.
  - **`harness/agent.py`:** the Harbor agent `harness.agent:Agent`.
    - Its settings include `policy` ("module:Class" or an object), `policy_args` (JSON) and `interface` (ours or legacy). Also `run_id`, `budget` and `counter`.
    - Policy hooks: `start(instruction, harness)`, `before_call(messages, harness)`, `before_tool`, `after_tool`, `report()`. `before_call` may edit the conversation in place and returns what to send.
    - A policy declares `budget` as "allowed", "required" or "refused".
    - `trajectory.json` records `policy`, `interface`, `end`, `end_P`, `final_turns` and `report`.
  - **`baselines/base/policy.py`:** `Base`, which does nothing; budget allowed.
  - **`baselines/summary/policy.py`:** `Summary`, the 10d logic unchanged, recording `compactions`; budget required.
  - **`econocontext/policy.py`:**
    - `Econo(mode=jev|prior)`, which wraps `Owner` unchanged and refuses a budget with "EconoContext has no budget mechanism yet";
    - `Directed`, which raises `NotImplementedError`.
- **`benchmarks/tblite/run.py`:**
  - `--policy` replaces `--arm`; `--interface` is new.
  - `POLICIES` maps each name to a class path and its arguments.
  - The Harbor command is `-a harness.agent:Agent --agent-kwarg policy=... interface=... run_id=...`.
  - The policies are checked before any trial runs.
  - Trial summaries read the trajectory's `report`.
- **Tests:**
  - shared fakes in `tests/harness/helpers.py` (`reply`, `FakeModel`, `LocalEnv`, `Counts`, `BySize`, `notices`, `tool_messages`);
  - the `make` fixture in `tests/conftest.py` (default policy `Base()`, interface `ours`).

### Test status at this commit

82 pass. 3 fail and 2 modules fail to import. All of them are tests not yet ported to the new layout:
- `tests/harness/test_agent.py` (import error): it still imports `agents.react.agent`.
- `tests/gateway/test_compaction_log.py` (import error): it still imports `tests.agents.test_react_agent`.
- `tests/benchmarks/test_run.py`: two tests still use `--arm` and the old arm names.
- `tests/measure/test_tokens.py::test_a_rebuilt_summary_prompt_renders_and_counts`: it still imports `agents.react.agent`.

Separately, `tests/unit/test_isolation.py` passes only because it scans `agents/`, which no longer exists. It must be pointed at `harness/` (see the remaining work).

### Remaining work for step 11 (in order)

**1. Port the tests**, no behaviour change.

a. **`tests/harness/test_agent.py`:**
- Import `Agent`, `TEMPLATE` from `harness.agent`, `OURS`, `LEGACY`, `TOOLS` from `harness.interface`, `cap` from `harness.tools` and `FINAL_TURN` from `harness.budget`.
- Remove the local helpers and the local `make` fixture; both are now shared.
- Make these changes:

  | old | new |
  |---|---|
  | `EconoAgent(...)` | `Agent(..., policy=Base())` |
  | `NUDGE` | `OURS.nudge`, or pass `interface="legacy"` and use `LEGACY.nudge` |
  | `agent.final_turns` | `agent.gate.final_turns` |

- Move the "budget with EconoContext is refused" test to `tests/econocontext/test_policy.py`.
- Move the four Summary tests to `tests/baselines/test_summary.py`, using `policy=Summary()` and `agent.policy.compactions`, with `CODEX_*` taken from `baselines.summary.policy`.
- In the capped-summary test, which uses `max_tokens=50`, pass `interface="legacy"`. Under OURS every reply of 50 tokens counts as cut, so no tool call would run.

b. **`tests/gateway/test_compaction_log.py`:**
- Take `LocalEnv` from `tests.harness.helpers`.
- Build the agent as `Agent(..., run_id="t1", policy=Econo(mode="prior", force=["7 compact"]))`.
- `agent.owner` → `agent.policy.owner`.
- The summary test uses `policy=Summary()`.

c. **`tests/measure/test_tokens.py`:** take `OURS.system` and `TOOLS` from `harness.interface`, `TEMPLATE` from `harness.agent` and `CODEX_SUMMARY_PREFIX` from `baselines.summary.policy`.

d. **`tests/benchmarks/test_run.py`:**
- `--arm` → `--policy`;
- `arm_name("base", 32000) == "base-32k"`;
- the summary error message is "the summary arm needs --budget";
- add a test that `--policy directed` refuses to run.

e. **`tests/unit/test_isolation.py`:**
- `ours()` scans `harness` and `baselines`, skipping `*/upstream/`.
- Add these checks:
  - `harness/` imports neither `econocontext` nor `baselines` (AST), and doesn't name them in code;
  - `econocontext/` never imports `baselines`.

f. **New tests in `tests/harness/test_interface.py`:**
- Every policy (`Base`, `Summary` with a budget, `Econo(mode="prior")`) sends the identical system prompt and tools.
- `OURS.system` has no context-management words (context, budget, compact, summary).
- Only the first tool call runs, and extra calls get `OURS.reject_extra`.
- A reply with no tool call gets `OURS.nudge`; a cut reply (`finish="length"`, or `completion_tokens` at or above `max_tokens`) with no tool call gets `OURS.nudge_cut`.
- A cut reply's tool call is not run and gets `OURS.cut_call`.
- `legacy` runs every call, cut ones included, with the old texts.

g. **New tests in `tests/econocontext/test_policy.py`:**
- `Econo` refuses a budget.
- `Directed` raises.
- An `Econo` prior-mode run writes `session.sqlite3` and its `report()`.

**2. Other updates:**
- `measure/report.py`: add `base` and `base-32k` to `ARM_ORDER`.
- `pyproject.toml`: in `[tool.setuptools.packages.find] include`, replace `agents*` with `harness*` and `baselines*`. This is safe because `baselines/clm` has no `__init__.py`.
- `README.md`: `harness/` replaces `agents/`, and `baselines/` now holds base, summary and clm.
- `docs/tier-a-decisions.md`: add a step-11 section covering the layout, the OURS interface texts and rules, the isolation rules, and the legacy interface.

**3. Commit and run the validation (one run, 1 repeat, `--budget 32000`, `--interface ours`).**
- Run all the tests until they pass, then commit. That commit is the candidate interface.
- Start the gateway, then run:
  ```
  .venv/bin/python benchmarks/tblite/run.py --label tc11v --policy base --budget 32000 --tasks bandit-delayed-feedback,scan-linux-persistence-artifacts --repeats 1 --model qwen3.8:27b --workers 1 --gates --progress runs/tier-a/step11-progress.md
  .venv/bin/python benchmarks/tblite/run.py --label tc11v --policy summary --budget 32000 --tasks bandit-delayed-feedback --repeats 1 --model qwen3.8:27b --workers 1 --gates --progress runs/tier-a/step11-progress.md
  ```
- **The clm-32k comparison.**
  - bandit: use tc10s's clm-32k run.
  - scan-linux: no clm-32k run exists at the 2,048 cap. The previous session had decided to run clm-32k on scan-linux once (`--policy clm --budget 32000`) for a like-for-like comparison, and to say so in the report. Check with Kavin if unsure.
- **Report per run, next to clm-32k:**
  - the share of calls at the 2,048 cap;
  - cuts right after a nudge;
  - steps used, pass and end reason;
  - PFLOPs and validation (`measure.report`).

  Adapt `runs/tier-a/scripts/step10e_think_where.py` and `step10e_cap_diag.py`. Note that our nudge text has changed: OURS.nudge starts "Your last reply made no tool call" and OURS.nudge_cut starts "Your last reply was cut off".

**4. Freeze rule.** If Base's share of calls at the cap is under about 15%, freeze the interface: record the candidate commit in `docs/tier-a-decisions.md`. If it is higher, stop and report; don't iterate.

**5. Budget unit (existing logs only, no new runs).**
- Use the CLM calls of tc9 (`runs/gateway/tc9.clm-32k.*` and `tc9.clm-full.*`) and of 10d (`runs/gateway/tc10s.clm-32k.*`).
- For each call, compute CLM's own count: in `.venv-clm`, run `clm_harness.utils.tokens.count_tokens` on the logged request messages (tiktoken o200k). Compare it with the exact Qwen prompt (`usage.prompt_tokens`, which our rebuild matches).
- Report:
  - the median ratio;
  - CLM's real Qwen peak at its 32k setting (the largest `prompt_tokens` over the clm-32k calls);
  - if that peak is above 29,952, the `context_budget_tokens` value that would match our limit.
- Background: CLM gates on `int(tiktoken × tok_ratio) > budget − 2,048`, where `tok_ratio` is learned from the server's `prompt_tokens` (`budget.py`).

**6. Finish.** Make sure the tests pass, commit, then stop with the step-11 report.
