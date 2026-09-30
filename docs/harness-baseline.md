# Harness baseline: versions and checks (2026-09-30)

The state of the repository before Gemini CLI support was added. Every later commit
on `feature/gemini-omnigent` must keep these numbers green; the existing
`openai-agents` path must not change.

| What | Value |
|---|---|
| Commit | `035caf8b7b181d9d61a5646e135ee24958cf24af` (main) |
| Python | 3.12.7 |
| Omnigent | 0.15.0 (built 2026-09-22) |
| Gemini CLI | 0.62.0, installed with `npm install --prefix data/tools @google/gemini-cli@0.62.0` |
| Node | v25.2.1 |
| `pytest -q` | 55 passed, 1 deselected |
| `pytest omnigent_layer/tests -q` | 23 passed |

Gemini CLI 0.62.0 facts checked in its bundle, not assumed:
- ACP flag: `--acp` (`--experimental-acp` is deprecated).
- In Vertex mode (`GOOGLE_GENAI_USE_VERTEXAI=true`) it reads the base URL from
  `GOOGLE_VERTEX_BASE_URL` (any valid URL, http allowed). With an API key it does not
  add `projects/<p>/locations/<l>`: requests go to
  `<base>/v1beta1/publishers/google/models/<model>:<method>`.
- `GEMINI_CLI_TRUST_WORKSPACE=true` is needed headless (otherwise it refuses an
  untrusted folder).

Upstream check (2 calls, 2026-09-30): `AGENT_PLATFORM_API_KEY` works on Vertex's native
endpoint without a project in the path:
`https://aiplatform.googleapis.com/v1beta1/publishers/google/models/gemini-3.6-flash:streamGenerateContent?alt=sse`
returned 200. Usage arrives in the last SSE chunk; `thoughtsTokenCount` is reported
outside `candidatesTokenCount` (7 prompt + 1 candidates + 91 thoughts = 99 total).

## Claude Code (2026-09-30)

| What | Value |
|---|---|
| Claude Code | 2.1.286, `npm install -g @anthropic-ai/claude-code@2.1.286` → `/opt/homebrew/bin/claude` (Omnigent's `claude-native` needs ≥ 2.1.161 on PATH). Runs set `DISABLE_AUTOUPDATER=1` so the version stays fixed |
| Omnigent harness | `claude-native` (the real CLI, driven in tmux 3.7c) |
| Bundled in `claude-agent-sdk` 0.2.161 | Claude Code 2.1.284 (not used) |

Key checks for Claude Sonnet 5 on Vertex (2 calls, 2026-09-30), with `AGENT_PLATFORM_API_KEY`:
- Native Claude endpoint (`.../publishers/anthropic/models/claude-sonnet-5:rawPredict`,
  what Claude Code's Vertex mode calls): **401**, "API keys are not supported by this
  API. Expected OAuth2 access token or other authentication credentials". It needs
  OAuth, i.e. a Google service account or `gcloud` credentials, not an API key.
- OpenAI-compatible endpoint (`.../locations/global/endpoints/openapi/chat/completions`,
  model `anthropic/claude-sonnet-5`): the key is accepted, but **400**, "Publisher Model
  `.../claude-sonnet-5` is not servable in region global".
