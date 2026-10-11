"""Step 10e: what each agent sends (system prompt, tools, task message) and its first two turns, bandit at 32k."""
import json

from measure.flops import prompt_ids
from measure.models import MEASURED
from measure.tokens import tokenizer

tok = tokenizer(MEASURED)
ntok = lambda s: len(tok.encode(s, add_special_tokens=False))  # noqa: E731
TEMPLATE = {"reasoning_effort": "medium", "chat_template_kwargs": {"enable_thinking": True}}
for arm in ("raw-32k", "clm-32k"):
    rows = [json.loads(l) for l in open(f"runs/gateway/tc10s.{arm}.bandit-delayed-feedback.r1/calls.jsonl")]
    req = rows[0]["request"]
    msgs, tools = req["messages"], req.get("tools") or []
    print(f"\n==================== {arm}")
    print(f"system prompt: {ntok(msgs[0]['content'])} tokens")
    print("   " + msgs[0]["content"][:600].replace("\n", "\n   ") + (" ..." if len(msgs[0]["content"]) > 600 else ""))
    base = len(prompt_ids(tok, {"messages": msgs[:2], "tools": None, **TEMPLATE}))
    full = len(prompt_ids(tok, {"messages": msgs[:2], "tools": tools, **TEMPLATE}))
    print(f"tools: {len(tools)}; the rendered tools block adds {full - base} tokens to the prompt")
    for t in tools:
        f = t["function"]
        print(f"   {f['name']}: {ntok(json.dumps(t))} tokens as JSON; description: {f.get('description')!r}; "
              f"parameters: {sorted((f.get('parameters') or {}).get('properties', {}))}")
    print(f"task message: {ntok(msgs[1]['content'])} tokens; first prompt P {rows[0]['usage']['prompt_tokens']}")
    print("   " + msgs[1]["content"][:300].replace("\n", "\n   ") + " ...")
    for i in (0, 1):
        r = rows[i]
        u, m = r["usage"], r["response"]["choices"][0]["message"]
        think = (u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
        calls = [(c["function"]["name"], ntok(c["function"]["arguments"])) for c in m.get("tool_calls") or []]
        print(f"turn {i + 1}: output {u['completion_tokens']} tokens = thinking {think} + visible {u['completion_tokens'] - think}; "
              f"finish {r['response']['choices'][0]['finish_reason']}; content {ntok(m.get('content') or '')} tokens "
              f"{(m.get('content') or '')[:160]!r}; tool calls (name, argument tokens) {calls}")
        reasoning = m.get("reasoning_content") or m.get("reasoning") or ""
        print(f"   thinking starts: {reasoning[:220]!r}")
