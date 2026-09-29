# Omnigent spike: findings (2026-09-28)

What we tested before building EconoContext on Omnigent 0.15.0, on the only model we
have today: Gemini 3.6 Flash through Vertex (`AGENT_PLATFORM_API_KEY`), via Vertex's
OpenAI-compatible endpoint. Evidence lives in `logs/gateway/calls.jsonl` and
`logs/probe/events.jsonl` (gitignored; no prompts or keys are quoted here).

## Answers

| # | Question | Answer | Evidence |
|---|---|---|---|
| 0b | Does the Vertex OpenAI endpoint report cache reads? | **Yes.** Same 14,236-token prompt twice: the 2nd and 3rd replies report `prompt_tokens_details.cached_tokens = 10,212`. On the 1st, `prompt_tokens_details` is **absent** (not zero). Reasoning tokens are reported **outside** `completion_tokens` (14,236 + 1 + 31 = 14,268 = `total_tokens`) | direct probe, 3 calls |
| 0a | Which API does the `openai-agents` harness call? | **Responses** (`/v1/responses`) by default. With `executor.use_responses: false` in the root spec it calls `/v1/chat/completions`, streams, and works end to end through the gateway | gateway log: refused `/responses`, then 200 on `/chat/completions` |
| 1 | Does a `tool_result` replacement reach the model? | **Yes, in Direct mode (`openai-agents`).** A policy that appended a marker to a `sys_os_read` result: the marker is inside the `tool` message of every later request (7 of 7) | gateway request bodies |
| 2 | Does `llm_request` carry the full prompt; does a replacement work? | **No and no.** `llm_request.data` is metadata only: `model`, `messages_count`, `tools_count`, `system_prompt_preview`, `last_user_message`. A `data` replacement had no effect (marker in 0 requests). The full prompt is visible only at the gateway | probe log, gateway bodies |
| 3 | Session identity in policy events | **No session id.** Stamping into `session_state` works, but the state a policy sees differs by phase: `request` events saw none of it, `llm_request` saw only its own key, tool events saw all keys. **Design consequence:** our policy gets its run id from `factory_params`, not from session state | probe log |
| 4 | Sub-agents | (a) A per-agent `auth.base_url` **is honored**: the sub-agent called `/run/<id>/agent/helper/v1/…`, so the gateway attributes calls per agent. (b) A sub-agent's own `policies:` are **not evaluated**: its tool events reach the **root** agent's policy. (c) A sub-agent **ignores** `use_responses: false` and calls `/responses`; setting `HARNESS_OPENAI_AGENTS_USE_RESPONSES=false` on the runner does not reach it either. After the refusal Omnigent retried the sub-agent on a Databricks model, which Vertex rejected (400). **So on Gemini today, `openai-agents` sub-agents do not work**; the bench uses a single agent | gateway log, probe log |
| 5 | Per-call usage from Omnigent | **Not available.** `llm_response` never fired in any run (0 events), and `context.usage` is a session total. **The gateway is the only per-call usage source**, and it has the cache fields (0b) | probe log |
| 6 | SWE-bench workspace end to end | **Yes.** `pytest-dev__pytest-5809`, econo arm, observe mode: **resolved** by the official harness. 28 model calls; 73% of input tokens were cache reads; 1,339-byte patch (3 files, all part of the fix). The write barrier bumped each changed path, and 4 of 6 stored file reads were invalidated when their files changed. 83 decisions logged, none applied (observe) | run `q6b:econo:pytest-dev__pytest-5809`; `bench/report.py --label q6b` |
| — | Native mode (Pi) | **Not answered today.** Pi 0.87.1 installed. `pi-native` ignores the spec's `auth`; it needs an Omnigent *provider*. A project-level `.omnigent/config.yaml` is not read (the runner's cwd is elsewhere). With a temporary user-level provider the session started, but Pi made no model call through the gateway within 110 s, and its tmux screen was not reachable to see why. The user-level config was restored afterwards | runner logs |

Other things we hit:
- **Client-side tools (`runtime: client`) are not dispatched to the Python client** for
  sessions whose runner the host daemon launched: the runner answered `testbed_shell not
  in local dispatch table`, and the agent fell back to the host shell. Fixed by making
  `testbed_shell` a server-side function tool (`omnigent_layer.tools.container_shell`),
  which the runner calls in the session's working directory.
- The agent's own `sys_os_shell` runs on the host, under macOS seatbelt by default
  (writes confined, reads not): when the container shell was broken it ran
  `find / -name pytest`. The bench prompt now says to use `testbed_shell` for all code.
- Omnigent's runner creates an untracked `mtime-test-*` directory in the workspace; the
  bench excludes it (`.git/info/exclude`) so it stays out of patches and versions.
- Omnigent's context compaction calls `/v1/responses/compact` even when the agent uses
  Chat Completions. The gateway refuses it (404); the sessions continued. Long runs
  should be checked for what compaction does after that refusal.
- Headless sessions need the host daemon to launch a runner; the bench uses the same
  (private) helpers `omnigent run` uses. Pinned to 0.15.0.

## What this means for the design

- **Tool-result control: yes** in Direct mode (`openai-agents`). POINTER at admission
  and the write barrier work through the policy.
- **Prompt shaping stays at the gateway, exact operators only.** Policies cannot see or
  change the prompt.
- **With Gemini's implicit cache there are no breakpoints or lifetimes to set**, so the
  gateway's active lever is mostly prefix stabilization.
- **On this setup the real value is measurement (gateway, per call, with cache fields),
  tool-level operators (policy), and placement** (later: router, fork, continue).
- **Attribution:** calls per agent from the gateway URL; tool events per run (root) from
  the policy.
- **Sub-agents on Gemini need a fix first**: Omnigent does not pass `use_responses` to
  inline sub-agents. Options: an upstream fix, or a gateway that also speaks the
  Responses API. A design decision, not taken here.
- Deep Agents is removed (tagged `deepagents-host` in git).
