"""Observer-only SSE assembly. Raw stream bytes remain the authoritative record."""

import json


class StreamResponse:
    def __init__(self):
        self.lines = []
        self.choices = {}
        self.metadata = {}
        self.done = False
        self.invalid = False

    def feed(self, line: bytes):
        if line.strip() == b"":
            self.flush()
        elif line.startswith(b"data:"):
            self.lines.append(line[5:].strip())

    def flush(self):
        if not self.lines:
            return
        data, self.lines = b"\n".join(self.lines), []
        if data == b"[DONE]":
            self.done = True
            return
        try:
            chunk = json.loads(data)
            self.metadata.update({k: v for k, v in chunk.items() if k != "choices"})
            for c in chunk.get("choices", []):
                index = c.get("index", 0)
                choice = self.choices.setdefault(index, {"index": index,
                                                         "message": {"role": "assistant"}})
                message = choice["message"]
                for key, value in (c.get("delta") or {}).items():
                    if key == "tool_calls":
                        calls = message.setdefault("tool_calls", {})
                        for part in value:
                            tool = calls.setdefault(part.get("index", 0), {"type": "function", "function": {}})
                            for field, val in part.items():
                                if field == "function":
                                    for name, fragment in val.items():
                                        tool["function"][name] = tool["function"].get(name, "") + fragment
                                elif field != "index":
                                    tool[field] = val
                    elif isinstance(value, str) and key != "role":
                        message[key] = message.get(key, "") + value
                    elif value is not None:
                        message[key] = value
                if c.get("finish_reason") is not None:
                    choice["finish_reason"] = c["finish_reason"]
        except (ValueError, TypeError, AttributeError):
            self.invalid = True

    def body(self):
        self.flush()
        # Copy before replacing the indexed tool fragments with a list.
        choices = json.loads(json.dumps([self.choices[i] for i in sorted(self.choices)]))
        for choice in choices:
            calls = choice["message"].get("tool_calls")
            if isinstance(calls, dict):
                choice["message"]["tool_calls"] = [calls[i] for i in sorted(calls, key=int)]
        return {**self.metadata, "choices": choices}
