"""Step 10e: where the long thinking happens, for our agent (raw) and CLM, bandit."""
import json
import re
import statistics
from collections import Counter

NUDGES = ("Continue by calling a tool", "(No tool call in your last turn")


def before(r):
    """What the model saw last before this call: a nudge, a tool result, the task, or a notice."""
    last = r["request"]["messages"][-1]
    text = str(last.get("content") or "")
    if last["role"] == "tool":
        return "tool result", len(text)
    if text.startswith(NUDGES):
        return "nudge", len(text)
    if text.startswith("[SYSTEM NOTICE]") or "CONTEXT BUDGET" in text:
        return "budget notice", len(text)
    return "task" if len(r["request"]["messages"]) <= 2 else "other user message", len(text)


for label, arm in (("tc10s", "raw-32k"), ("tc10s", "clm-32k"), ("tc9", "raw"), ("tc9", "clm-32k")):
    paths = [f"runs/gateway/{label}.{arm}.bandit-delayed-feedback.r{n}/calls.jsonl" for n in (1, 2, 3)]
    rows = []
    for p in paths:
        try:
            rows += [json.loads(l) for l in open(p)]
        except FileNotFoundError:
            pass
    rows = [r for r in rows if r.get("status") == 200 and r.get("kind") == "agent"]
    cap = lambda r: r["usage"]["completion_tokens"] >= r["request"]["max_tokens"]  # noqa: E731
    think = lambda r: (r["usage"].get("completion_tokens_details") or {}).get("reasoning_tokens") or 0  # noqa: E731
    by_before = {}
    for r in rows:
        kind, _ = before(r)
        by_before.setdefault(kind, []).append(think(r))
    runs_of_caps, current = [], 0
    for r in rows:
        current = current + 1 if cap(r) else (runs_of_caps.append(current) or 0) if current else 0
    if current:
        runs_of_caps.append(current)
    multi = sum(len(r["response"]["choices"][0]["message"].get("tool_calls") or []) > 1 for r in rows)
    tools = Counter(c["function"]["name"] for r in rows for c in r["response"]["choices"][0]["message"].get("tool_calls") or [])
    print(f"\n== {label} {arm}: {len(rows)} agent calls, {sum(map(cap, rows))} at the cap; runs of consecutive cut calls: "
          f"{sorted(runs_of_caps, reverse=True)}; replies with 2+ tool calls: {multi}; tools used: {dict(tools)}")
    for kind, xs in sorted(by_before.items()):
        print(f"   thinking after a {kind:18s}: n {len(xs):3d}, median {statistics.median(xs):6.0f}, max {max(xs):5d}, "
              f"share > 2,048: {sum(x > 2048 for x in xs) / len(xs):4.0%}")
    if label == "tc10s":
        for r in [r for r in rows if cap(r)][:3]:
            m = r["response"]["choices"][0]["message"]
            th = m.get("reasoning_content") or m.get("reasoning") or ""
            words = Counter(w.lower() for w in re.findall(r"\b(wait|hmm|actually|but|let me|alternatively)\b", th, re.I))
            print(f"   cut call {r['call_no']} after a {before(r)[0]}: thinking {len(th)} chars; hedges {dict(words)}")
            print(f"      starts: {th[:200]!r}")
            print(f"      ends:   {th[-200:]!r}")
