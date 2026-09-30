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

## Why Omnigent's cost figure differs from ours (2026-09-29)

For the SWE-bench run, Omnigent reports **$0.2586**. The ledger, pricing what Vertex
reported at the gateway, says **$0.0977**. We are not measuring wrong. Two things on the
way from Vertex to Omnigent drop information:

1. **The cache discount is lost to a field name.** Omnigent's `openai-agents` executor
   looks for cached tokens in `usage.prompt_tokens_details`
   (`omnigent/inner/openai_agents_sdk_executor.py`, around line 1866). The Agents SDK
   stores them as `input_tokens_details` (`agents/models/openai_chatcompletions.py:159`).
   Omnigent therefore always sees 0 cached tokens and bills all input at full price.
2. **Reasoning tokens are left out.** Vertex reports reasoning outside
   `completion_tokens`; the SDK copies `completion_tokens` as the output. Omnigent's
   pricing is otherwise cache-aware (`compute_llm_cost`, MLflow catalog prices, which
   match our card at $0.75/M input).

The reproduction is exact: 338,454 input × $0.75/M + 1,261 output × $3.75/M =
$0.25856925, Omnigent's figure to the last digit. Our figure prices the same run's
90,039 fresh + 248,415 cached input and 3,082 output (reasoning included). It is still
our own calculation; the final check is the Google Cloud billing console.

This is also why the gateway exists: Omnigent's usage is summed per turn by the
harness, after the harness has converted it, and policies never see per-call usage.
The gateway reads what the provider itself returned, for every call.

## Workers, and continuing one (2026-09-29)

| Check | Answer |
|---|---|
| Can a worker (sub-agent) run on Gemini? | **Yes, through a provider.** A sub-agent with `auth: {type: provider, name: econo}` and a provider set to `wire_api: chat` calls Chat Completions, at the gateway's `/current/agent/worker/v1` |
| Are two workers told apart? | **Yes.** The gateway names each worker by its first message (its task): `<run>:worker:<hash>`. A continued worker keeps its id |
| Does reusing a title continue a worker? | **Yes.** The second task reached the same worker id, with its earlier exchange in the request (4 messages) |
| Can EconoContext choose to continue a worker? | **Yes.** A policy may replace a `sys_session_send` call's arguments. It must return the bare arguments dict, not `{name, arguments}`. Rewriting the title made Omnigent continue the worker the root had not named |

Other things learned:
- **A policy that fails to load or raises makes Omnigent deny the action** (it fails closed). EconoContext's policy catches every error and abstains, so it cannot block an agent.
- **Omnigent's server keeps a policy module loaded.** After editing `omnigent_layer/`, restart Omnigent.

## When EconoContext changes what the model sees, does the harness know? (2026-09-29)

Two different places change content, with different answers:

| Where | What changes | Does the harness know? |
|---|---|---|
| **Policy** (POINTER when a tool result arrives) | Omnigent stores the replaced result | **Yes.** The harness's own history holds the pointer text, so every later request, the transcript and the UI agree. This is the path that ran live (30 times in Phase 3) |
| **Gateway** (COMMIT_PENDING, ZONED: rewriting a request on its way to the model) | Only the copy sent to the model | **No.** The harness keeps the full text in its history and re-sends it on every call |

Why the gateway path is still consistent for the model:
- The gateway stores each replacement (`gateway_pointers`, keyed by run, agent and tool-call id) and re-applies it to **every** later request from that agent.
- So the model sees one stable history, and the cached prefix stays stable after the one-time change.
- The full text is saved in the workspace, where the agent can reopen it.

Where it can diverge, and what happens:
- **Harness compaction or its own token counting** work on the full text. The harness may compact earlier than needed: harmless, and afterwards the replaced message is simply gone.
- **The transcript and UI** show the full text, not what the model saw. What was actually sent is in the Agent DB (decisions, manifest, gateway_pointers).
- **Stateful model APIs** (server-side conversation state, e.g. Responses with `previous_response_id`) would break this. Our harness uses stateless Chat Completions, where every request carries the whole history.
- **Losing the database** would lose the replacements, and the model would see the full text again. That is correct but more expensive, and it breaks the cache once.

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

## Stock Gemini CLI over ACP (2026-09-30)

Gemini CLI 0.62.0, Omnigent's generic `acp` harness, Vertex through the gateway's
`/run/<id>/gemini` route. Smoke task (`bench/gemini/smoke.py`): **resolved**, 6 model
calls, $0.031, 52% of input tokens cache reads. What it took:

| Problem | Cause | Fix |
|---|---|---|
| `harness 'acp' is not configured on host` | The host daemon launches `acp` only when an ACP agent is registered in `~/.omnigent/config.yaml`, even though the spec embeds its own | `bench/setup_provider.py` also registers Gemini CLI. The spec's embedded agent is what runs |
| `did not answer authenticate within 30s` | In ACP mode Gemini takes its auth type only from settings (`security.auth.selectedType`), not from `GOOGLE_GENAI_USE_VERTEXAI`, so `session/new` failed. Omnigent's fallback then sent `authenticate` with `gemini-api-key` (the first non-browser method), which never returns. `GEMINI_CLI_SYSTEM_SETTINGS_PATH` was not enough | Each run gets its own `HOME`, with `bench/gemini/settings.json` as `~/.gemini/settings.json` (`vertex-ai`). It also keeps each run's Gemini session files apart |
| Turn ended at the first edit, empty reply | Gemini asked permission for `replace`. Omnigent raised an approval card despite `permission_mode: bypassPermissions` (a policy verdict of ASK wins over bypass), and the headless client ended the turn | `gemini --acp --approval-mode yolo`: Gemini does not ask. ACP `tool_call` events still reach Omnigent |

Observed from the requests (not assumed):
- Gemini CLI's own tools: `read_file`, `list_directory`, `glob`, `grep_search`,
  `replace`, `write_file`, `run_shell_command`, `web_fetch`, `google_web_search`,
  `invoke_agent` (its sub-agents), `update_topic`, `enter_plan_mode`, `activate_skill`,
  `list_background_processes`, `read_background_output`.
- `functionCall` and `functionResponse` parts carry matching `id`s.
- The system instruction is ~32 KB; the first request was 10,051 prompt tokens.
- In Vertex mode with an API key, requests go to
  `/v1beta1/publishers/google/models/gemini-3.6-flash:streamGenerateContent?alt=sse`.
  The session model is `gemini-3.6-flash` (`GEMINI_MODEL`). Gemini's "auto" router
  (Pro and newer Flash) is not used.

## Can Omnigent see and address Gemini's own sub-agents? (2026-09-30)

`bench/gemini/probe.py`, one run (`probe-gemini:econo:subagents-20260930T193842`, pytest
repository). The task asked how pytest collects tests and did not name a sub-agent.
Gemini delegated on its own first call: `invoke_agent` with
`agent_name: codebase_investigator`. The sub-agent then made 10 model calls (1 grep,
6 file reads, the rest retries). The session ended when Omnigent's ACP idle deadline
fired (below).

| # | Question | Answer | Evidence |
|---|---|---|---|
| 1 | Does ACP expose `invoke_agent`? | **Not observed.** The failed turn left no tool-call items in Omnigent (only `resource_event`, `message`, `error`). In a completed session (smoke), ACP tool calls do reach Omnigent's transcript, but named by their ACP *title* (`calc.py`, `'**/*.py'`), not the tool name | `session_view` of both sessions |
| 2 | Is `agent_name` visible? | **In the model traffic and in Gemini's files, not in Omnigent.** It is the `functionCall` argument that the next root request carries, and it is in Gemini's session file | Gemini main session file; gateway |
| 3 | Does the child get its own ACP session id? | **No.** Omnigent created no child session. Gemini gives the child its own internal session id (`kind: subagent`), in its own files only | `child_sessions` = []; `~/.gemini/tmp/.../chats/<parent>/<child>.jsonl` |
| 4 | Are child model calls visible at the gateway? | **Yes.** All of them, through the same `/run/<id>/gemini` route | 11 model spans |
| 5 | Can child calls be told from root calls? | **Yes, by content.** The child has its own `context_key` (its own system instruction), and its own model: **`gemini-3.8-flash`** (the root runs `gemini-3.6-flash`; `GEMINI_MODEL` does not reach it). No price card exists for 3.8 Flash, so its calls are recorded with cost incomplete, never priced as 3.6 | span metadata, `outcomes.cost_complete` |
| 6 | Does ACP expose the child's start and end? | **No.** While the child worked, Gemini sent Omnigent no ACP update for over 300 s. Omnigent's ACP idle deadline (`HARNESS_ACP_PROMPT_TIMEOUT_S`, default 300) then ended the turn: "Timeout waiting for ACP response". The generic `acp` harness has no Gemini sub-agent source (`omnigent/inner/acp_subagents.py`) | runner log |
| 7 | Can the child be addressed after it finishes? | **No.** Omnigent has no handle for it, and `invoke_agent` takes only `agent_name` and `prompt`, with no id to continue | as 3; Gemini's tool arguments |
| 8 | Can it be resumed? | **No**, for the same reasons | |
| 9 | Can Omnigent redirect the delegation? | **No.** Gemini runs `invoke_agent` itself. With `--approval-mode yolo` it asks no permission, and an Omnigent policy cannot rewrite a native tool's arguments over ACP | |

**Outcome B: worker placement is unsupported for Gemini.** Its sub-agents run inside
the Gemini process as loops of their own, but they are not sessions Omnigent can list,
continue or resume. EconoContext does not emulate them. Worker placement stays on the
controlled `openai-agents` path. On Gemini, what can be measured is everything the
gateway sees, per conversation loop: calls, tokens, cost, evidence, acquisition turns.

Two practical consequences:
- **A delegating Gemini session needs a longer ACP idle deadline.** Set
  `HARNESS_ACP_PROMPT_TIMEOUT_S` (e.g. 1800) in the environment of the Omnigent
  process that launches runners. The spec has no field for it.
- **Gemini retries a failed model call with the identical request.** The gateway marks
  such a call `retry_of` and does not count its evidence again.

Also found: Gemini asks permission for some shell commands even with
`--approval-mode yolo`. The headless client declined the resulting approval card and
the turn ended (the first `pytest-5809` run stopped after 7 calls). Gemini runs now
accept approval cards (`run_session(approve=True)`).
