# What is measured on a Gemini CLI run

Every Gemini CLI model call passes the gateway's `/run/<run_id>/gemini` route and is
forwarded byte for byte. Only the credential changes (placeholder → real key); the
body is untouched. What the gateway records per call, and where:

| Field | Where | From |
|---|---|---|
| run_id, agent_id (`<run>:root`) | `outcomes`, `runtime_spans` | URL (or `X-Econo-Run-ID`); sub-agents are not told apart yet |
| call_id | `outcomes.outcome_id` = `runtime_spans.native_id` | new per call |
| timestamp, latency | `runtime_spans.started_at`, `duration_ms`; `outcomes.latency_ms` | gateway clock |
| model | `runtime_spans.name` | request path (`models/<model>:<method>`) |
| request_hash, request_bytes | span `metadata` | sha256 and length of the body as received |
| prefix_hash | span `metadata` | everything but the newest content: equal on consecutive calls means the history was only appended to |
| contents | span `metadata` | number of `contents` entries |
| context_key | span `metadata` | system instruction + first user content (tells a sub-agent's loop apart) |
| call_no | span `metadata` | model calls the run made before this one |
| function_responses | span `metadata` | tool results new in this request: `{name, args_key, bytes}` |
| response_calls | span `metadata` | tool calls the reply asked for: `{name, kind, args_key}` |
| response_text | span `metadata` | whether the reply said anything besides tool calls (thoughts do not count) |
| input, cached, output, thinking tokens | `outcomes` | `usageMetadata` (`gemini_wire.to_usage`) |
| cost (USD and NU), complete | `outcomes` | ledger, priced by the card of the model used; no card → incomplete |

`kind` is `file`, `search`, `write`, `meta` or `other` (`gemini_wire.TOOLS`). An
unknown tool is `other`, never a safe read.

**Not stored:** prompt text, tool output text and model output. Hashes and sizes
replace them. `ECONO_GATEWAY_LOG_BODIES=1` saves whole request bodies under
`logs/gateway/bodies/`, only for building test fixtures. `logs/` is gitignored.

**Other Gemini paths** (`countTokens`, `embedContent`, anything unknown) are passed
through and logged in `logs/gateway/calls.jsonl`. They are not model calls: no span,
no outcome, and they do not count toward the call cap.
