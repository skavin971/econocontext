# Official cache-read prices, as a share of the input price (retrieved 2026-10-13)

Prices per 1M tokens, standard tier, from each provider's own pricing page on the date above. "Share" = cache read ÷ input.

| provider | URL | examples (input → cache read) | share |
|---|---|---|---|
| OpenAI | https://developers.openai.com/api/docs/pricing (redirect target of platform.openai.com/docs/pricing; openai.com/api/pricing returned 403) | gpt-6-astra $10.00 → $1.00; gpt-5.5 (<272K) $5.00 → $0.50; gpt-5.4-mini $0.75 → $0.075; gpt-5.3-codex $1.75 → $0.175; **gpt-6.1-sol $2.00 → $0.10** | **10%** on most models; **5%** on gpt-6.1-sol |
| Anthropic | https://platform.claude.com/docs/en/about-claude/pricing (redirect target of docs.anthropic.com) | page text: "Cache read (hit): 0.1x base input price (0.025x on Claude Fable 5.1 and Claude Mythos 5.1; 0.05x on Claude Opus 5.5 and Claude Sonnet 5.5)"; e.g. Sonnet 4.6 $3 → $0.30, Opus 5.5 $4 → $0.20, Fable 5.1 $10 → $0.25. Cache writes cost 1.25x (5 min) or 2x (1 h) | **10%** standard; **5%** Opus 5.5 / Sonnet 5.5; **2.5%** Fable / Mythos 5.1 |
| Google Gemini API | https://ai.google.dev/gemini-api/docs/pricing (page "last updated 2026-10-13 UTC") | Gemini 2.5 Flash $0.30 → $0.03; Gemini 2.5 Pro (≤200k) $1.25 → $0.125; Gemini 3.8 Flash $0.75 → $0.075; Gemini 3.1 Pro Preview $2.00 → $0.20; **Gemini 4 Argon $2.00 → $0.10**. Explicit caching also charges storage per hour | **10%**; **5%** on Gemini 4 Argon |
| DeepSeek | https://api-docs.deepseek.com/quick_start/pricing | **not loaded directly** (three attempts timed out); from the official page's search-result text: V4-Flash cache hit $0.0028 vs cache miss $0.14; V4-Pro $0.003625 vs $0.435 (marked promotional by third parties) | **2%** (V4-Flash); **0.8%** (V4-Pro); unverified |
| Together AI (open-model host) | https://www.together.ai/pricing | Kimi K3 $3.00 → $0.30; Qwen3.8-2.4T-A95B $2.00 → $0.25; DeepSeek V4 Pro 0813 $1.32 → $0.13; Muse Glimmer 30B $0.35 → $0.04; MiniMax M3 $0.30 → $0.06 | **10–12.5%** for most; 20% MiniMax M3 |

Also checked: Fireworks (https://fireworks.ai/pricing) says serverless "Usage is billed on output, input, and cached tokens" but lists no cached-input rate on that page.

The Complexity Trap's own cards, for comparison: Qwen3-Coder 40% of input; Qwen3-32B 100% (no discount); Gemini 2.5 Flash through litellm 1.77 25%, though their logged Gemini costs carry no discount (step 18).
