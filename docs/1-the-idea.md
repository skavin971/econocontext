# 1. The idea

## The problem: agents pay for the same text again and again

An LLM API is stateless. It remembers nothing between calls, so on every call an
agent re-sends its whole context window: system prompt, tool descriptions, the
task, and every earlier message, tool call and tool result. A coding agent that
takes 40 steps sends its early file reads 40 times.

Most of the bill is this re-sending, not the thinking. In our 5-instance check
(baseline arm, 255 model calls), the agent sent about 75 input tokens for every
output token it generated, and input was 79% of the cost, even with caching.

## Why "use fewer tokens" is the wrong rule

Tokens are not all priced the same. Gemini 3.6 Flash on Vertex AI, until 2026-12-31:

| Billing category | Price per 1M tokens | In NU |
|---|---|---|
| Uncached input (sent fresh) | $0.75 | 1.0 |
| Cache read (a prefix the provider already has) | $0.075 | 0.1 |
| Output (including reasoning) | $3.75 | 5.0 |

- A 20,000-token prompt that is mostly cached can cost less than an 8,000-token prompt sent fresh.
- Cutting text out of the middle of a prompt can make the bill go *up*, because everything after the cut is no longer the same prefix, so it stops being served from the cache.

So cost has to be predicted in billing categories, not in token counts. EconoContext
works in **NU** (normalized units): 1 NU is the price of one uncached input token
of the model in use. Output is 5 NU per token and a cache read is 0.1 NU per
token. Only a price-card file changes between providers; the logic does not.

## What EconoContext does about it

The agent keeps deciding **what** to do: which file to read, which fix to try.
EconoContext only decides **how** a step is carried out, and only when there is
a way that is cheaper and still correct:

- **A repeated tool call:** the agent reads the same file again and the file hasn't changed. Instead of running the tool, return the stored output, byte for byte.
- **A large tool result:** instead of putting the whole result into the window, where it is re-sent every turn, put a short preview and a file path the agent can reopen.
- **A repeated subagent task:** the agent asks a subagent to do the same task again and nothing it read has changed. Return the stored answer instead of starting a new subagent.
- **The order of a prompt:** order it so the stable parts come first, where the provider's cache can serve them.

For each step it lists the options, removes the incorrect ones, predicts the cost
of the rest, picks the cheapest, and logs everything, including why every other
option lost.

## Five rules the design never breaks

1. **Correctness first.** An option that could give the agent stale or incomplete information when exact bytes are needed is removed by a gate before any price is looked at. It is never "a bit cheaper but a bit wrong".
2. **The host's own behaviour is always an option.** EconoContext can always decline to change anything. If nothing better qualifies, the host does exactly what it would have done alone.
3. **Fail-open.** If any part of EconoContext crashes, the agent carries on as if EconoContext were not there. The error is logged.
4. **No invented numbers.** Every price comes from a provider page, recorded with its URL and date. Every other constant lives in `config/econocontext.yaml` with a comment saying where it came from. Unverified values are `null`, marked `# TODO: verify`.
5. **Predicted next to actual.** Every decision records what it expected to cost. Every model call records what the provider actually billed. The two can be joined, so the predictions can be checked and improved (the "closed loop").

## What EconoContext is not

- **It is not an agent.** It has no loop and no tools of its own, and it never decides what the agent works on.
- **It is not a summarizer.** It never rewrites content. The only approximate change it can make is a pointer (a preview plus a path), and that is switched off by default.
- **It is not tied to one harness or provider.** The core never imports Omnigent, a harness or a provider SDK, and a test enforces this. Everything platform-specific lives in `omnigent_layer/`.

Next: [2. How it fits into an agent](2-how-it-fits.md)
