# Step 10d smoke: Base, Summary and CLM at a 32,000-token budget (2026-10-11)

**Setup:**
- Label `tc10s`.
- Tasks `acl-permissions-inheritance` and `bandit-delayed-feedback`, one repeat each, run one at a time.
- Every arm runs on `qwen3.8:27b` on Purdue, through the gateway with step 10b's settings: temperature 0.7, top_p 0.95, thinking on, reasoning_effort medium, max_tokens 2,048.
- The arms:
  - `raw-32k` (Base): CLM's budget rule.
  - `summary-32k`: Codex-style compaction at 24,000 tokens, then the Base rule.
  - `clm-32k`: CLM's own agent with `context_budget_tokens=32000`.
- Commands and outputs: `runs/tier-a/step10d-smoke-*.out`. Measures: `runs/tc10s/report.csv`, `runs/tc10s/calls.csv` and `runs/tier-a/step10d-report.out`.

## Per run

| task | arm | result | calls | compactions or CLM edits | why the run ended | PFLOPs | validated | calls at the cap |
|---|---|---|---|---|---|---|---|---|
| acl | raw-32k | pass | 4 | – | submitted | 0.127 | 4/4 | 0 |
| acl | summary-32k | pass | 4 | 0 compactions (never reached 24,000) | submitted | 0.115 | 4/4 | 0 |
| acl | clm-32k | pass | 7 | 0 edits | CLM's agent finished | 0.324 | 7/7 | 0 |
| bandit | raw-32k | fail | 45 | – | submitted in the final turn: at call 45 the prompt was 30,013 > 29,952, so CLM's notice was appended | 5.424 | 45/45 | 23 |
| bandit | summary-32k | fail | 65 (64 agent + 1 summary) | 1 compaction | max_steps (64) | 8.781, of which 1.409 is the summary call | 65/65 | 30 |
| bandit | clm-32k | fail | 16 | 1 edit, 2 nudges, 0 rollbacks | CLM's agent finished (not at its budget or call cap) | 2.372 | 16/16 | 1 |

**Validation.** Every one of the 141 calls validated token for token against the server's prompt ids, either identical or differing only by a tools-block key reorder.

## The compaction (summary-32k, bandit)

| | value |
|---|---|
| prompt before (our count) | 24,606 (trigger: 24,000) |
| compaction call's prompt (our count / server) | 24,227 / 24,227: no tools block, plus Codex's prompt |
| summary output tokens | 1,933, finish `stop`: under the 2,048 cap, but at 94% of it (thinking included) |
| prompt right after, [system, task, summary] | 4,561 = 14.2% of the 32k budget |
| summary call validated | identical |
| first call after (#33) validated | tools block only, keys reordered (ΔP 0) |

The compaction call carried no tools (Codex sends none), so it shared no prefix with the agent's prompts and was prefilled in full: 1.409 of the run's 8.781 PFLOPs.

## One call at the output cap per arm, and what the agent did next

| arm | the cut call | what came next |
|---|---|---|
| raw-32k | bandit call 8: 2,048 tokens, `length`; 7,526 characters of thinking, no content, no tool call | our nudge: "Continue by calling a tool. Call `submit` when the task is fully done." |
| summary-32k | bandit call 8: 2,048 tokens, `length`; 5,913 characters of thinking, no tool call | the same nudge |
| clm-32k | bandit call 5: 2,048 tokens, `length`; 6,574 characters of thinking, no tool call | CLM's own nudge: "(No tool call in your last turn. When ready, issue your next `bash` command to continue the task.)" |

**How the cut calls break down:**

| arm | cut while thinking (`length`, then a nudge) | cut tool calls (`tool_calls`) |
|---|---|---|
| raw-32k | 16 | 7 |
| summary-32k | 24 | 6 |
| clm-32k | 1 | 0 |

**What a cut tool call does.** It runs with whatever parameters were complete. Base's call 22 was `bash` with `{}`, so the agent ran an empty command, which returned "(exit code 0)". Summary's call 19 had its whole command parameter and ran normally.

## Findings

- **The 2,048 cap costs our agent many steps on long tasks.**
  - On bandit, 23 of Base's 45 calls and 30 of Summary's 65 hit the cap, most of them cut while thinking. Each such call is lost to a nudge.
  - The Summary run then ran out of its 64 steps. CLM's agent hit the cap once in 16 calls.
  - As recorded in step 10b, 2,048 cuts our agent more often than CLM. The 8,192 sensitivity run will show how much of the gap this explains.
- **The Base rule worked as CLM's code does.** The final turn was granted at 30,013 tokens, and the model used it to submit.
- **The Summary compaction behaved as specified.**
  - The trigger fired at 24,606 ≥ 24,000.
  - Our count of the compaction call was the server's, exactly.
  - The summary was not capped, but it used 94% of the cap.
  - The rebuilt prompt was 14.2% of the budget.
  - The calls on either side of the compaction validated.
