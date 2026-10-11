# Codex's compaction prompt (SUMMARIZATION_PROMPT), verbatim: the Summary baseline sends it as a user message
#   after the conversation (CLM: "the summary harness uses the Codex summarization prompts").
# Source: openai/codex, tag rust-v0.146.0 (Codex CLI 0.146.0, the version CLM cites), commit e363b08c9175ac1cbe5893615dd2cb9ddf95043b,
#   codex-rs/prompts/templates/compact/prompt.md (codex-rs/prompts/src/compact.rs includes it).
# License: Apache License 2.0 (LICENSE-codex in this folder); Copyright 2025 OpenAI (NOTICE-codex). Redistributed
#   unmodified, with the license and notice, as Apache-2.0 allows (step 10d).
# Lines up to and including '# ---' are this header; everything after it is the text, byte for byte.
# ---
You are performing a CONTEXT CHECKPOINT COMPACTION. Create a handoff summary for another LLM that will resume the task.

Include:
- Current progress and key decisions made
- Important context, constraints, or user preferences
- What remains to be done (clear next steps)
- Any critical data, examples, or references needed to continue

Be concise, structured, and focused on helping the next LLM seamlessly continue the work.
