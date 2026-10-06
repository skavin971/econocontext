# How v2 runs differ from Harbor's stock `claude-code` agent (both arms alike)

1. **Driver:** the Claude Agent SDK talks to `claude` in the container through `docker exec -i`, instead of `claude --print`, so a compaction can interrupt a turn. It uses Claude Code's own system prompt (the `claude_code` preset), default tools, and `bypassPermissions`, the same as Harbor.
2. **Claude Code pinned to 2.1.290** (Harbor installs the latest by default).
3. **Hooks are command hooks** (`curl` to the host service), not HTTP hooks: 2.1.290 blocks HTTP hooks to private addresses. Econo arms only.
4. **Workers as in plain Claude Code:** Harbor's `FORCE_AUTO_BACKGROUND_TASKS=1` and `ENABLE_BACKGROUND_TASKS=1` are removed (user's decision, 2026-10-05). A worker's report returns as the Agent tool's result.
5. **The model key never enters the container:** `ANTHROPIC_API_KEY` is a placeholder, and the gateway's Anthropic route adds the real key and records usage and cost.

## Measurement note

**Run cost = transcript usage priced with the card.** The gateway is a cross-check and can undercount. In `v2x:baseline:maven-slf4j-conflict`, the transcript has 21 calls and the gateway 20. The missing one is the first response, cut off mid-stream (4 output tokens recorded). The gateway logs usage only for completed streams, but the call's input is still billed: about 31.9k cache-read + 3.9k cache-write tokens ≈ $0.016, which is exactly the gap ($0.5937 vs $0.5774).
