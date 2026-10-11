"""Shared fakes for the harness and policy tests: a scripted model that returns real openai response
objects, a local shell standing in for the task's container, and scripted prompt counters. No network."""

import copy
import json
import subprocess
from types import SimpleNamespace

from openai.types.chat import ChatCompletion


def reply(*calls, text=None, finish="tool_calls", extra=None, completion_tokens=50):
    message = {"role": "assistant", "content": text}
    if calls:
        message["tool_calls"] = [{"id": f"call_{i}_{name}", "type": "function",
                                  "function": {"name": name, "arguments": json.dumps(args)}}
                                 for i, (name, args) in enumerate(calls)]
    if extra:
        message["extra_content"] = extra
    completion = ChatCompletion.model_validate({
        "id": "r", "object": "chat.completion", "created": 0, "model": "gemini",
        "choices": [{"index": 0, "finish_reason": "stop", "message": message}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": completion_tokens, "total_tokens": 1000 + completion_tokens,
                  "prompt_tokens_details": {"cached_tokens": 600}}})
    # Vertex returns finish reasons outside OpenAI's list (malformed_function_call); the SDK
    # does not validate replies strictly, so set it after building, as a real reply carries it.
    completion.choices[0].finish_reason = finish
    return completion


class StatusError(Exception):
    def __init__(self, status, text=""):
        super().__init__(text)
        self.status_code = status


class FakeModel:
    def __init__(self, script):
        self.script, self.sent, self.headers, self.tools = list(script), [], [], []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **kwargs):
        self.sent.append(copy.deepcopy(kwargs["messages"]))
        self.headers.append(kwargs.get("extra_headers"))
        self.tools.append(kwargs.get("tools"))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class LocalEnv:
    """Runs commands with local bash in `root`."""
    def __init__(self, root):
        self.root, self.commands = root, []

    async def exec(self, command, timeout_sec=None, **kwargs):
        self.commands.append(command)
        if command.startswith("timeout "):
            command = command.split(" ", 4)[4]   # drop "timeout -k 5 N"
        done = subprocess.run(["bash", "-c", command], cwd=self.root, capture_output=True, text=True)
        return SimpleNamespace(stdout=done.stdout, stderr=done.stderr, return_code=done.returncode)


class Counts:
    """A scripted prompt counter: one value per count."""
    def __init__(self, values):
        self.values = list(values)

    def __call__(self, messages, tools=None):
        return self.values.pop(0)


class BySize:
    """Tokens by (number of messages, tools sent?): a test knows which prompt each count is for."""
    def __init__(self, table):
        self.table = table

    def __call__(self, messages, tools=None):
        return self.table[(len(messages), tools is not None)]


def tool_messages(agent):
    return [m["content"] for m in agent.messages if m["role"] == "tool"]


def notices(messages):
    return [m["content"] for m in messages if m["role"] == "user" and m["content"].startswith("[SYSTEM NOTICE]")]
