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
| 6 | SWE-bench workspace end to end | **Yes.** `pytest-dev__pytest-5809`, econo arm, observe mode: **resolved** by the official harness. 28 model calls; 73% of input tokens were cache reads; 1,339-byte patch (3 files, all part of the fix). The write barrier bumped each changed path, and 4 of 6 stored file reads were invalidated when their files changed. 83 decisions logged, none applied (observe) | run `q6b:econo:pytest-dev__pytest-5809`; `harness/report.py --label q6b` |
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
`/run/<id>/gemini` route. Smoke task (`harness/smoke.py`): **resolved**, 6 model
calls, $0.031, 52% of input tokens cache reads. What it took:

| Problem | Cause | Fix |
|---|---|---|
| `harness 'acp' is not configured on host` | The host daemon launches `acp` only when an ACP agent is registered in `~/.omnigent/config.yaml`, even though the spec embeds its own | `harness/setup.py` also registers Gemini CLI. The spec's embedded agent is what runs |
| `did not answer authenticate within 30s` | In ACP mode Gemini takes its auth type only from settings (`security.auth.selectedType`), not from `GOOGLE_GENAI_USE_VERTEXAI`, so `session/new` failed. Omnigent's fallback then sent `authenticate` with `gemini-api-key` (the first non-browser method), which never returns. `GEMINI_CLI_SYSTEM_SETTINGS_PATH` was not enough | Each run gets its own `HOME`, with `harness/specs/gemini/settings.json` as `~/.gemini/settings.json` (`vertex-ai`). It also keeps each run's Gemini session files apart |
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

`harness/probe.py`, one run (`probe-gemini:econo:subagents-20260930T193842`, pytest
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
| 9 | Can the delegation be redirected? | **Not over ACP, but yes through Gemini's own hooks** (corrected below). Gemini runs `invoke_agent` itself, and an Omnigent policy cannot rewrite a native tool's arguments over ACP. Gemini CLI's `BeforeTool` hook can block the call or rewrite its arguments, and it is configured in the per-run settings file | Gemini bundle: `BeforeToolHookOutput.getModifiedToolInput`, blocking decisions |

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

### Correction: other harnesses, and Gemini's own hooks (2026-09-30)

Checked in the installed source (Omnigent 0.15.0, Gemini CLI 0.62.0), not live:
Codex is not installed here, needs OpenAI auth, and calls the Responses API, which
Vertex does not serve.

- **Omnigent does reach native sub-agents, for some harnesses.** `claude-native`,
  `codex-native`, `opencode-native` and `devin-native` declare `subagents=True`; the
  generic `acp` harness (Gemini) does not. The mechanism is not ACP: Omnigent installs
  a pre-tool hook in the harness (`omnigent/inner/hook_scripts/subagent_router.py`;
  Codex: `PreToolUse` on `spawn_agent`; Claude Code: on its Task tool). The hook can
  rewrite the spawn (model, effort) or deny it, and the denial tells the model to use
  Omnigent's `sys_session_create` instead. A routed worker is then an Omnigent session,
  which can be continued like our `openai-agents` workers. Omnigent turns this on for
  its Smart Routing sessions.
- **Gemini CLI has the same kind of hook.** A `BeforeTool` hook can block a tool call
  ("Tool execution blocked: <reason>" goes back to the model) or rewrite its
  arguments (`hookSpecificOutput.tool_input` is merged into the call). A `BeforeModel`
  hook can rewrite the model request. Hooks are set in Gemini's settings, which each
  run already gets its own copy of, so no fork is needed.
- **What stays impossible without changing Gemini:** continuing one of Gemini's *own*
  sub-agents. `invoke_agent` has no id to continue; each call starts afresh.
- **Not yet verified live:** that `BeforeTool` fires for `invoke_agent` in ACP mode, and
  whether a sub-agent's own tool calls go through hooks.

So the probe's Outcome B holds for Gemini's own sub-agents. Two routes are open
through Gemini's hook, both decisions rather than facts:
1. **Rewrite `invoke_agent`** (stays native): e.g. give a new sub-agent the evidence an
   earlier one already acquired.
2. **Route delegation to Omnigent workers** (as Omnigent does for Codex and Claude): deny
   `invoke_agent` and point the model at `sys_session_create`/`sys_session_send`
   (needs `omnigent_mcp: true`). Workers become addressable and EconoContext's
   existing RESUME applies, but Gemini no longer delegates in its own way.

## Claude Code (claude-native) with Claude Sonnet 5 (2026-09-30)

Claude Code 2.1.286 on Omnigent's `claude-native` harness. Its model calls go to the
gateway's `/run/<id>/anthropic` route, and from there to Anthropic's API with a key
only the gateway holds. Vertex could not be used: its Claude endpoint refuses API keys,
and the DBAI project has no Claude Sonnet quota (`docs/harness-baseline.md`).

| Check | Answer | Evidence |
|---|---|---|
| Does per-run config reach Claude Code? | **Yes, through the workspace's `.claude/settings.local.json`** (env: `ANTHROPIC_BASE_URL`, model pins; `apiKeyHelper` placeholder; tool permissions). Omnigent passes its own hooks with `--settings` and loads the user, project and local sources | smoke run: every call reached the gateway |
| Does only Sonnet 5 get called? | **Yes, once every model setting is pinned.** Claude Code makes small background calls (under 1,300 input tokens) besides the agent's; all were `claude-sonnet-5` | gateway log |
| Do the user's own settings leak in? | **No.** The user's `model` is overridden by the local settings; none of the user's synced skills appear in the request | request bodies |
| What does Claude Code send? | `thinking: adaptive`, `effort: medium`, `max_tokens: 64000`, `context_management: clear_thinking (keep all)`, mid-conversation `role: "system"` messages, a ~30.7k-token cached prefix (system + tools). Tools include `Agent` (its sub-agents), `Bash`, `Read`, `Edit`, `Write`, `ToolSearch`; Grep, Glob and every MCP tool are deferred behind `ToolSearch` (Omnigent sets `ENABLE_TOOL_SEARCH`) | request bodies |
| Does a turn end when Claude Code finishes? | **No.** Omnigent completes a `claude-native` turn when the prompt is typed into the terminal. `harness/session.py` waits until the session is busy and then idle again | runner log |
| Do Omnigent tools reach Claude Code? | **Only Omnigent's built-in ones** (29 `mcp__omnigent__*`), not our function tool `testbed_shell`. Claude Code runs the repository's tests with `docker exec` from its own Bash instead | request bodies |
| Without the per-run settings? | Omnigent falls back to the **local Claude login** ("Claude CLI login (subscription provider 'claude')") and the gateway sees nothing. It happened once (c1: the workspace was recreated after the settings were written); runs now fail loudly (`check_settings`) or end "not measured" (`check_measured`) | c1 |

Runs (one task each):
- **Smoke** (`calc.py`): resolved in 6 calls, $0.10, 74% of input from cache.
- **c2** (`pytest-dev__pytest-5809`, econo arm, observe mode): **resolved** in 15 calls, $0.21,
  **91% of input tokens were cache reads** (407k read, 40k written, 1.7k fresh). The planner
  logged a decision on every call (AS_IS each time; nothing applied in observe mode).
  Oracle: 2 of 15 turns only read, worth about $0.015 (7% of the run). Claude Code could
  not run the tests in this run (the docker exec instruction came after it).
- **c3** (same task, same arm, with the docker exec instruction): **resolved** in 10 calls,
  $0.15, 86% of input from cache. Claude Code ran the repository's tests in the container
  itself ("All pastebin tests pass"). Tools asked for: Read ×3, Edit ×2, Bash ×2. Oracle: 3 of
  10 turns only read, worth about $0.023 (15% of the run).

### Autopilot on Claude Code requests (2026-09-30)

What the gateway may change on a Claude Code request, and nothing else:
- **COMMIT_PENDING only.** An old tool result is replaced by a pointer (a head/tail preview
  and the path of a file holding the full text, which Claude Code can Read). The same
  replacement is repeated in every later request of the run (`gateway_pointers`), so the
  cached prefix is stable again after the one change. Thinking blocks, message order,
  roles and `cache_control` are left exactly as Claude Code sent them.
- **Never ZONED or retrieval.** Claude Code runs always set `allowlist.ZONED: false`.
- **Fallback.** Editing earlier turns can be refused on some models and accounts (Anthropic's
  "preserved thinking" check). If Anthropic answers 4xx to a changed request, the gateway
  sends Claude Code's original instead (Claude Code never sees the error), forgets the
  run's pointers and changes nothing more in that run (`*stopped*` in `gateway_pointers`,
  and an `autopilot_stopped` line in the gateway log).


- **c4** (same task, **autopilot**, `--pointer`, ZONED off): **resolved** in 10 calls, $0.12,
  92% of input from cache. **Nothing was changed.** COMMIT_PENDING was feasible on one call
  and lost on cost: with the prefix already cached, replacing an old result would have
  broken the cache for everything after it (predicted 360,884 NU against 360,690 NU for
  leaving the request alone). On short, well-cached runs, shortening history does not pay;
  the pricing model says so and the gateway obeyed. No refusal from Anthropic was needed.

### Sub-agents on Claude Code: what the request itself says (2026-09-30)

From the `Agent` tool's definition in Claude Code 2.1.286's requests (no model call needed):
- Sub-agents are started with `Agent` (`description`, `prompt`, `subagent_type`, optional
  `model`, `isolation`). `subagent_type: "fork"` starts one that inherits the whole parent
  conversation (and so its cached prefix).
- **A sub-agent can be continued**: "To continue a previously spawned agent, use SendMessage
  with the agent's ID or name as the `to` field — that resumes it with full context."
  `ListAgents` lists the agents that can be messaged. Gemini CLI has nothing like this.
- `model` accepts `sonnet`, `opus`, `haiku`, `fable`. The model pins cover the first three,
  not `fable`, so the gateway now refuses any model but `claude-sonnet-5` on this route
  (`ECONO_ANTHROPIC_MODELS`), instead of sending it.

### Can Omnigent and EconoContext see and address Claude Code's sub-agents? (2026-09-30)

`harness/probe.py --harness claude-code`, on the pytest repository. The task asked Claude
Code to use a sub-agent and then continue that same one. The first attempt was stopped
early by our runner: Claude Code ran the sub-agent in the background and ended its own
turn, and the runner took the idle session for done. It now waits for 30 s of real quiet,
with no busy session, no busy sub-agent and no new model call. Second attempt
(`probe-claude-code:econo:subagents-20260930T214221`): 19 calls, $0.27, every call
`claude-sonnet-5`.

| # | Question | Gemini CLI | **Claude Code** | Evidence (Claude Code) |
|---|---|---|---|---|
| 1 | Is the delegation visible to Omnigent? | not observed | **Yes**: an `Agent` tool call in the session's items | `session_view` |
| 2 | Is the sub-agent named? | in model traffic only | **Yes**: Omnigent's child session is titled `general-purpose:<agent id>` | child sessions |
| 3 | Does the sub-agent get its own session? | no (Gemini-internal only) | **Yes**: an Omnigent child session, and Claude Code's own `subagents/agent-<id>.jsonl` transcript | child sessions, `~/.claude/projects/...` |
| 4 | Are its model calls visible at the gateway? | yes | **Yes** | 12 calls |
| 5 | Can they be told from the root's? | yes (context, model) | **Yes**: its own `context_key`; same model (pinned) | span metadata |
| 6 | Are its start and end visible? | no | **Yes**: the child session's task status (`completed`); the sub-agent ends each phase with `SubagentHandback` | child sessions, gateway |
| 7 | Can it be addressed after it finishes? | no | **Yes, by Claude Code**: `SendMessage` to the agent's id (loaded through `ToolSearch`) | root calls 12–13 |
| 8 | Can it be continued? | no | **Yes, with its context and its cache**: after the follow-up the sub-agent's first call read 24,516 tokens from cache and wrote 1,985; the same child session and conversation key continued | calls 14–17 |
| 9 | Can EconoContext steer it? | not over ACP | **Not tried yet.** Two ways are open: an Omnigent policy may deny an `Agent` call with a reason ("continue agent X with SendMessage"; Omnigent honors a deny on `PreToolUse`), or Omnigent's sub-agent router for Claude Code's spawns | — |

**Outcome A for Claude Code.** Its sub-agents can be told apart, seen starting and
ending, and continued with their cache, which is what worker placement (RESUME) prices.
Worker placement on Claude Code is therefore feasible. The remaining work is the
steering: let the policy turn a new `Agent` call into a continuation when the pricing
model says an idle worker already holds the files.

## Worker placement on Claude Code: first RESUME experiment (2026-09-30)

**Question.** When Claude Code has already decided to delegate a task to a new sub-agent,
is it cheaper to continue one of its existing, idle sub-agents instead, and why?

**Rule of the experiment.** Claude Code decides whether to delegate and what to delegate.
Nothing in the task mentions sub-agents. EconoContext only changes where an
already-decided delegation runs:
- **control:** every `Agent` call goes through, so Claude Code creates a new worker. The
  placement decision is logged, not carried out (observe mode).
- **econo:** when the pricing model prefers an idle worker (RESUME), the policy denies the
  new `Agent` call. Its reason names the worker and asks Claude Code to send the same
  prompt there with `SendMessage`. Claude Code then does so itself.

**How it works** (`omnigent_layer/claude_workers.py`, `policy.py`):
- **Workers, from the requests at the gateway.** An `Agent` call plus the `agentId` in its
  result. Its loop is the conversation whose first user message is the `Agent` prompt,
  matched by hash (no prompt text is stored). A worker is idle after `SubagentHandback`.
  What it holds is the files its loop read, and its last context size.
- **Placement, in the Omnigent policy on Claude Code's `PreToolUse` for `Agent`.**
  `engine.plan_placement` prices FRESH against RESUME for any idle worker of the same
  type (`need_named_files=False`). Forks are never redirected. Omnigent maps the
  policy's DENY to Claude Code's `permissionDecision: deny` with the reason.
- **Runs:** `harness/probe.py --harness claude-code --resume --mode observe|autopilot
  --task … --followup …`, with the Omnigent server restarted to load the policy.

**Finding a task that delegates on its own** (each outcome is a finding too):

| Run | Task | Delegations | Result |
|---|---|---|---|
| wctl | SWE-bench `sphinx-doc__sphinx-8593` (bug fix) | **none**: Claude Code did it alone (Bash ×11, Read ×4, Edit ×4) | resolved, 22 calls, $0.32 |
| wctl2 | "Write docs/autodoc-architecture.md…" (four parts) | **one** Explore agent (17 calls, $0.24 of the run's $0.34) | note written; its last 4 calls were refused by the gateway's daily token cap (fixed) |
| wctl3 | the same, plus a follow-up | — | every call refused by the daily token cap; not measured. The cap counted cache reads and now applies to the Vertex routes only |
| **wctl4** | the note, then in the same session: "Now add a section … on inherited members … and how it documents properties and class attributes" | **two** Explore agents, one per message | control arm |
| **wres4** | the same two messages | **one** Explore agent; the second `Agent` call was redirected, and Claude Code continued the first worker with `SendMessage` (loaded via `ToolSearch`) | econo arm |

A follow-up request in the same session is ordinary Claude Code use. It is what made a
second delegation happen while the first worker was idle, which is the only situation
where RESUME applies. A single bug fix did not delegate at all.

**Results.** Both runs finished the document: control 199 lines, econo 219 lines, both
with the requested sections.

| | control (wctl4) | econo (wres4) |
|---|---|---|
| Whole run | 33 calls, $0.592 | 37 calls, $0.605 |
| Root loop | 14 calls, $0.298 | 14 calls, $0.271 (incl. the denied `Agent`, `ToolSearch`, `SendMessage`) |
| First delegation (worker 1, first message) | 10 calls, $0.176 (Bash ×8) | 11 calls, $0.159 (Bash ×14) |
| **Follow-up delegation** | **new worker: 5 calls, $0.100**; fresh 10, cache reads 84,862, cache writes 20,595, output 3,121; tools: Bash ×2, Read ×5 | **resumed worker 1: about 9 calls, about $0.164**; cache reads about 343,000, writes about 17,800, output about 4,400; tools: Bash ×9 |
| Each worker's extra call after its handback | 1 per worker, about $0.007 | 1, about $0.007 |

(The resumed worker's figures exclude its phase-1 trailing call; the loop totals are 10
calls and $0.171.)

**What the resumed worker already had.** In phase 1 it had run 14 Bash commands, naming
`sphinx/ext/autodoc/__init__.py`, `importer.py`, `directive.py`, `typehints.py`,
`sphinx/registry.py`, `sphinx/application.py` and `sphinx/util/typing.py`. Its first
continued call read 30,232 tokens from cache: its whole history was still warm. **In
phase 2 it went back to `autodoc/__init__.py` and `importer.py`, files it had already
touched, reading other line ranges** (`sed -n '140,244p'`, `'244,330p'`, …). The follow-up
was about different code (inherited members, properties) from the first task
(discovery, filtering, type hints).

**Why RESUME cost more here, although its cache was warm:**
1. **Warm history is cheap per token, not free, and it is paid on every call.** The resumed
   worker's calls each carried about 30–45k tokens of history at $0.20/M. Over 9 calls that
   was about $0.07 of cache reads, against about $0.02 for the new worker, whose calls
   started from the ~11k-token prefix shared by all of Claude Code's agents.
2. **It did not save the reading.** It had seen the files, but not the lines the new
   question needed, so it made as many acquisition calls as a new worker (9 against 7 tool
   calls), and more model calls (9 against 5).
3. **Output and cache writes did not shrink.** Output 4.4k against 3.1k; writes 17.8k
   against 20.6k.

**The pricing model's prediction was wrong, and in the wrong direction.** It chose RESUME
at 17,336 NU against 46,840 NU for FRESH (about 2.7× cheaper). The actual follow-up cost
1.6× more. Where the estimate goes wrong:
- **Calls:** both options are assumed to take 5 calls (a placeholder). A resumed worker's
  call count is not known to be smaller.
- **Context:** the resumed worker's resident context is priced once, at cache-read rate.
  In reality it grows with every call and is paid on each.
- **FRESH base:** a new worker is charged 9.3k fresh tokens per call. In reality Claude
  Code's shared prefix is already cached, so a new worker is cheaper than that.
- **Holdings:** credited as 0, because Explore reads files through Bash (`sed`, `grep`),
  which the evidence layer does not count as reads. Had it counted them, the estimate
  would have been *more* optimistic, since holding a file did not mean holding the lines
  needed.

**Conclusions, with their limits.**
- One pair of runs (n = 1 per arm), and Claude Code is not deterministic: the two first
  phases already differed (10 vs 11 calls). This shows a mechanism, not an average.
- **The mechanism works end to end:** Claude Code's own delegation was redirected to an
  existing worker, without prompting it to use sub-agents, and it used the redirect.
- **Resuming is not automatically cheaper on Claude Code.** It pays when the follow-up
  needs what the worker already read, not merely something from the same files. When the
  new task is different, a fresh worker with the shared cached prefix is cheaper.
- **To decide well, the placement price needs:**
  - measured call counts for a new vs a resumed worker, learned from labelled runs
    (not the placeholder)
  - resident context priced on every expected call
  - FRESH priced from Claude Code's real cached prefix
  - a relevance test between the new task and what the worker holds (overlap of the files
    **and line ranges** its history covers), with Bash reads (`sed -n`, `cat`, `grep`)
    counted as evidence
- Until then, RESUME should stay off (`allowlist.RESUME: false`, the default) for Claude
  Code runs.

Spend on the Anthropic key for all Claude Code work so far: **$2.84 of the $4.50 budget**.

## Cost model v2, step 0: a cold worker is never resumed (2026-09-30)

The plan for the next phase ("price = meter(forecast)") lives in `docs/7-cost-model-v2.md` once
written; its first step is this safety fix, which needs no model calls.

- **The bug.** `planner.for_placement` let a worker whose cache had expired be a RESUME
  candidate. It priced that worker's history at the full input rate (1.0×) instead of
  the 1.25× cache write it really triggers.
- **Why a cold resume can't win.** A cold resumed worker re-writes its whole history (about
  30K tokens, so 37.5K NU at 1.25×) on its first call. In the wres4 follow-up, that is
  already more than the new worker's entire 5-call run (about 37.7K NU). The new worker
  starts from the shared prefix, which is already cached.
- **The fix.** Only warm workers (last model call within `cache.ttl_seconds`, 300 s) are
  RESUME candidates. It covers both harnesses: the openai-agents worker map and Claude
  Code's `claude_workers`.
- **The number to check in the logs before any RESUME:** the worker's seconds since its
  last model call, against the cache TTL.
- **Every option's price was already logged.** Every decision, in every mode, logs its
  candidates' prices and sizes (`decisions.candidates`, `payloads`), so observe runs are
  calibration data. Nothing changed there.
- **Still open**, in the next steps:
  - no worker placement run on openai-agents has had a real decision yet
  - no option moves only the relevant held evidence to a new worker (HANDOFF)
