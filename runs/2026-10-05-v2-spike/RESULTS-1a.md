# Spike 1a: Claude Code hook mechanisms (2026-10-05)

Claude Code 2.1.286, `claude_agent_sdk` 0.2.161. Sonnet 5 through the gateway's Anthropic route (pass-through, run `spike1:baseline:host`). Hooks are `type: "http"`, going to `spike_hooks.py` on port 8790. Spend: **$0.26** (A $0.128, A2 $0.031, D $0.099).

| Check | Result | Evidence |
|---|---|---|
| a. A container reaches the host's hook server and gateway | **pass** | `check_a_network.out`: an alpine container POSTed to `host.docker.internal:8790/hook` and got the rewrite reply; `host.docker.internal:8787` answered (400 for an unsupported GET path). Docker Desktop reaches the gateway even though it listens on 127.0.0.1 |
| b. `PostToolUse` `updatedToolOutput` replaces a built-in tool's result | **pass, with the tool's own shape** | A plain string was **ignored** (session A). The object `{...tool_response, "stdout": preview}` replaced it (session A2: the model received exactly 3 preview lines). Bash's `tool_response` = `{stdout, stderr, interrupted, isImage, noOutputExpected}` |
| c. `PreToolUse` `updatedInput` rewrites a call | **pass** | Session A: `echo SPIKE_REWRITE original` ran as `echo REWRITTEN_BY_ECONO` |
| d. A driver can interrupt, send `/compact <instructions>`, and continue | **pass** | `sessionD.out`: interrupt → `error_during_execution`; `/compact` → success; continue → "Code word is BLUE-42". `PreCompact` got `custom_instructions`; `PostCompact` got the model-written `compact_summary`; the transcript has the boundary |
| e. The transcript has per-message usage with cache fields | **pass** | `input_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `output_tokens` (+ `thinking_tokens`) on every assistant message |
| f. The pass-through serves Sonnet 5 | **pass** | All sessions answered. Every hook input has `session_id` and `transcript_path` |

**Findings that change the design:**
- **Replacements must keep the tool's response shape** (an object for Bash; Read and others still to check in step 2 with recorded inputs).
- **The model notices silent substitutions and flags them as anomalies.** It reported "something in this environment replaced the output", and it didn't try to recover the rest because the preview didn't say how. So every replacement or note must **explain itself and say how to get the full content**, for example: "[EconoContext] 400 lines, full output saved at `.econocontext/out/<id>.txt`; read it if you need more."
- **Every Claude Code session starts by writing about 36,400 tokens to the cache** (system prompt + tools). That is a fixed prefix, and the prices must include it.
- **The Anthropic key's total cap is $4.50** (`ECONO_ANTHROPIC_BUDGET_USD`). $2.84 was spent before today, plus this spike's $0.26. The TBLite experiments need it raised.
