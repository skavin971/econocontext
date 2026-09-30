# 5. Omnigent and SWE-bench: the real test bed

EconoContext is tested on agents run by an unmodified, real platform, on real bugs,
with no fake models and no toy harness.

## The pieces

| Piece | What it is | Version / setting |
|---|---|---|
| **Omnigent** | Databricks' open-source meta-harness: it runs Claude Code, Codex, Pi, the OpenAI Agents SDK and others, with sessions, sub-agents, sandboxes, policies and a UI | `omnigent==0.15.0`, pinned (alpha: event fields change between releases) |
| **The harness** | Omnigent's `openai-agents` harness (Direct mode: Omnigent runs the tools itself) | `use_responses: false`, because Vertex serves Chat Completions only |
| **Gemini 3.6 Flash** | The model, through Vertex AI's OpenAI-compatible endpoint. Only the gateway holds the key (`AGENT_PLATFORM_API_KEY`) | Model id `google/gemini-3.6-flash` |
| **SWE-bench Verified** | 500 real GitHub issues from Python projects, each with hidden tests that pass only if the bug is fixed | `swebench==5.0.2` |
| **Docker** | Each instance has an official image with the repository at `/testbed` at the buggy commit, and its dependencies installed | linux/amd64, runs under emulation on Apple Silicon |

## The agent (`harness/specs/openai_agents.yaml`)

- **One spec for both arms.** A short prompt: fix the issue in the working directory; hidden tests will check it.
- **Its tools:**
  - Omnigent's own `sys_os_read`, `sys_os_write`, `sys_os_edit` and `sys_os_shell`. These run on the host, in the workspace.
  - `testbed_shell` (`omnigent_layer/tools.py`), which runs a command inside the instance's container, in the repository's own Python environment.
- **One path:** the agent is told to use relative paths, and `testbed_shell` shows the host path wherever `/testbed` appears. So the agent sees one path for the same files.
- **No sub-agents yet:** on our Gemini setup Omnigent's inline sub-agents call the Responses API (see [the findings](omnigent-findings.md)).

## How EconoContext hooks in (`omnigent_layer/`)

| Omnigent seam | EconoContext file | What it does |
|---|---|---|
| The agent's model URL (`executor.auth.base_url`) | `gateway.py` | `plan_prompt` on the full request (econo arm), forward with the real key, `record` exact usage, caps |
| A policy in the agent spec (econo arm only) | `policy.py` | `before_tool_call` (logged), `admit_tool_result` (a replacement reaches the model), `on_file_write` |
| — | `workspace.py` | `git status` in the workspace after each write or shell tool: which paths changed |
| A Python function tool | `tools.py` | The container shell |

**Converting the model's messages** (`wire.py`): each message of the Chat Completions
request becomes a segment.

| Message | Becomes a segment of kind |
|---|---|
| The tool list | `TOOLS` |
| A system (or developer) message | `SYSTEM` |
| The first user message | `TASK` |
| An assistant message with tool calls | `TOOL_CALL` (paired with its results by tool-call id) |
| A tool message | `TOOL_RESULT` |
| Anything else | `MESSAGE` |

When nothing is changed, the harness's request is forwarded byte for byte.

## The two arms

| | Baseline | Econo |
|---|---|---|
| Model calls through the gateway, measured | yes | yes |
| `plan_prompt` at the gateway | no | yes |
| EconoContext policy in the spec | **no** | **yes** |
| Everything else (model, prompt, tools, workspace, caps) | same | same |

Commands (the Omnigent server and the gateway must be running; see [TESTING.md](TESTING.md)):
- baseline: `.venv/bin/python benchmarks/swebench/run.py --label L --instance ID --arm baseline`
- econo: `.venv/bin/python benchmarks/swebench/run.py --label L --instance ID --arm econo --mode observe|autopilot [--jev]`

## Instances (fixed before any run)

- **Development:** `pytest-dev__pytest-5809`. It is kept out of the comparison set, so nothing is tuned to it.
- **Comparison set, chosen by rule:**
  - an easy fix (under 15 minutes)
  - a small Python repository with an image of about 1 GB
  - at least one regression test
  - five different repositories

| Instance | Tests that must start passing | Tests that must keep passing |
|---|---|---|
| `psf__requests-2317` | 8 | 133 |
| `pallets__flask-5014` | 1 | 59 |
| `pylint-dev__pylint-4970` | 1 | 17 |
| `pytest-dev__pytest-7432` | 1 | 77 |
| `sphinx-doc__sphinx-8721` | 1 | 3 |

## Grading (`benchmarks/swebench/evaluate.py`)

- **Who grades:** only the official SWE-bench harness decides pass or fail. It runs the hidden tests on the patch in a fresh container.
- **Without Docker:** it stops with a message; there is no homemade substitute.
- **Run ids:** each evaluation gets a new run id, because the harness caches results by run id.

## Limits

| Limit | Value | Enforced by |
|---|---|---|
| Model calls per run | 60 | The gateway (refuses the 61st) |
| Input tokens per day, all runs | 3,000,000 | The gateway |
| Shell command timeout in the container | 300 s | `testbed_shell` |
| Dollars per run | — | **Not yet**: this needs the ledger to be built |

## Results so far

**On Omnigent** (2026-09-28): dev instance, econo arm, observe mode.
- **Outcome:** resolved, 28 model calls, 73% of input tokens served from cache.
- **Decisions:** 83 logged, none applied.
- **Patch:** 3 files, all part of the fix.

**On the earlier Deep Agents host** (tag `deepagents-host`), five-instance pipeline check
`check5`:

| Arm | Resolved | Model calls | Cost |
|---|---|---|---|
| baseline | 5/5 | 255 | $1.879 |
| econo (autopilot) | 4/5 | 282 | $1.862 |

- **What these prove:** the pipeline works end to end and is measured.
- **What they don't prove:** savings. At temperature 1.0, runs vary more than EconoContext's effect so far.

Next: [6. Status and future](6-status-and-future.md)
