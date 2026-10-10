# vLLM 0.30 chat parsing (source: vllm-project/vllm v0.30.0, vllm/entrypoints/chat_utils.py), read 2026-10-10

- Assistant messages: `reasoning = message.get("reasoning")`; if present it is passed to the template as both
  `reasoning` and `reasoning_content`. An input `reasoning_content` is never read, so it is dropped.
- `_postprocess_messages`: tool-call `arguments` strings are `json.loads`ed into dicts (invalid JSON or a
  non-object becomes `{}`; empty or missing becomes `{}`); empty `tool_calls` lists are removed.
- `tool_call_id` is kept for tool messages only; `name` is kept for every role.

Probe evidence (runs/purdue-smoke/template-probe/, step1b-template-probe*.out):
- our agent's echo (`reasoning_content`): server renders an empty think block -> consistent with vLLM 0.30 itself.
- the same turn with `reasoning`: server still renders an empty think block -> Purdue's front end also strips
  `reasoning` (vLLM would have kept it).
- preserve_thinking: the server matches unset/true (an empty think block is rendered even before the last user
  message); false does not match.
- reasoning_effort: accepted as a top-level field and in chat_template_kwargs; "low" changes the server prompt.
