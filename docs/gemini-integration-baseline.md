# Gemini integration: baseline (2026-09-30)

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
