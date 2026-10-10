"""Read a Claude Code session transcript (the JSONL file at a hook's `transcript_path`).

Why it exists: hooks see one event at a time; the transcript has the whole conversation and
each model call's usage. The rules need the number of calls so far and the current prompt
size; Jev needs the conversation itself.
"""

import json
from pathlib import Path


def _lines(path: str | Path):
    try:
        with open(path) as f:
            for line in f:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except OSError:
        return


def calls(path: str | Path) -> list[dict]:
    """One usage dict per model call (Claude Code repeats a call's usage on each content block)."""
    seen: dict[str, dict] = {}
    for entry in _lines(path):
        message = entry.get("message") or {}
        if entry.get("type") == "assistant" and message.get("usage") and message.get("id"):
            seen[message["id"]] = message["usage"]
    return list(seen.values())


def usage_totals(paths) -> dict:
    """Token totals over transcripts (main agent and workers), each model call counted once."""
    return usage_totals_of([usage for path in paths for usage in calls(path)])


def usage_totals_of(usages) -> dict:
    """Token totals over a list of per-call usage dicts."""
    totals = {"calls": 0, "input": 0, "cache_read": 0, "cache_write_5m": 0, "cache_write_1h": 0, "output": 0}
    for usage in usages:
        created = usage.get("cache_creation") or {}
        one_hour = int(created.get("ephemeral_1h_input_tokens") or 0)
        totals["calls"] += 1
        totals["input"] += int(usage.get("input_tokens") or 0)
        totals["cache_read"] += int(usage.get("cache_read_input_tokens") or 0)
        totals["cache_write_1h"] += one_hour
        totals["cache_write_5m"] += int(usage.get("cache_creation_input_tokens") or 0) - one_hour
        totals["output"] += int(usage.get("output_tokens") or 0)
    return totals


def cost_usd(totals: dict, rates: dict) -> float:
    """Dollars from usage_totals and a billing_rates.yaml tier (per million tokens)."""
    return (totals["input"] * rates["input_per_mtok"] + totals["cache_read"] * rates["cache_read_per_mtok"]
            + totals["cache_write_5m"] * rates["cache_write_per_mtok"]
            + totals["cache_write_1h"] * rates.get("cache_write_1h_per_mtok", rates["cache_write_per_mtok"])
            + totals["output"] * rates["output_per_mtok"]) / 1e6


def prompt_tokens(usage: dict) -> int:
    return sum(int(usage.get(k) or 0) for k in
               ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))


def conversation(path: str | Path, max_chars: int = 200_000) -> list[dict]:
    """The conversation as compact turns: user text, assistant text, tool calls and results.
    Cut from the front when longer than max_chars (the recent part matters most)."""
    turns: list[dict] = []
    for entry in _lines(path):
        message = entry.get("message") or {}
        role, content = message.get("role"), message.get("content")
        if role not in ("user", "assistant"):
            continue
        if isinstance(content, str):
            turns.append({"role": role, "text": content})
            continue
        for block in content or []:
            kind = block.get("type")
            if kind == "text":
                turns.append({"role": role, "text": block.get("text", "")})
            elif kind == "tool_use":
                turns.append({"role": "assistant", "tool_call": block.get("name"),
                              "input": block.get("input")})
            elif kind == "tool_result":
                body = block.get("content")
                if isinstance(body, list):
                    body = "\n".join(b.get("text", "") for b in body if isinstance(b, dict))
                turns.append({"role": "tool", "result": str(body)})
    total, kept = 0, []
    for turn in reversed(turns):
        total += len(json.dumps(turn))
        if total > max_chars and kept:
            break
        kept.append(turn)
    return list(reversed(kept))
