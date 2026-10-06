"""The full-control agent (adapters/harbor/econo_agent.py) with a fake model and a local shell.

The fake environment runs the agent's real bash commands in a temporary directory (it only drops
the `timeout` prefix, which macOS lacks), so tools, cwd persistence and the output cap are real.
The fake model returns real openai response objects, scripted per test. No network.
"""

import asyncio
import copy
import json
import subprocess
from types import SimpleNamespace

import pytest
from openai.types.chat import ChatCompletion

from adapters.harbor.econo_agent import NUDGE, EconoAgent, cap


def reply(*calls, text=None, finish="tool_calls", extra=None):
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
        "usage": {"prompt_tokens": 1000, "completion_tokens": 50, "total_tokens": 1050,
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
        self.script, self.sent = list(script), []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **kwargs):
        self.sent.append(copy.deepcopy(kwargs["messages"]))
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


@pytest.fixture
def make(tmp_path, monkeypatch):
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda seconds: real_sleep(0))
    def build(script, **kwargs):
        agent = EconoAgent(logs_dir=tmp_path / "logs", model_name="openai/google/gemini-3.6-flash",
                           client=FakeModel(script), state_dir=str(tmp_path / "state"), **kwargs)
        env = LocalEnv(tmp_path / "work")
        (tmp_path / "work").mkdir(exist_ok=True)
        context = SimpleNamespace(n_input_tokens=0, n_cache_tokens=0, n_output_tokens=0)
        async def go():
            await agent.setup(env)
            await agent.run("Make a file.", env, context)
        asyncio.run(go())
        return agent, env, context
    return build


def tool_messages(agent):
    return [m["content"] for m in agent.messages if m["role"] == "tool"]


def test_tools_run_in_order_and_submit_ends_the_run(make, tmp_path):
    agent, env, context = make([
        reply(("bash", {"command": "mkdir sub && echo hi > sub/f.txt && cat sub/f.txt"})),
        reply(("write_file", {"path": "sub/g.txt", "content": "a\nb\nc\n"})),
        reply(("read_file", {"path": "sub/g.txt", "start_line": 2, "end_line": 3})),
        reply(("bash", {"command": "cd sub"})),
        reply(("bash", {"command": "pwd && ls"})),
        reply(("submit", {})),
    ])
    out = tool_messages(agent)
    assert out[0].startswith("hi\n(exit code 0)")
    assert "wrote 6 bytes to sub/g.txt" in out[1]
    assert out[2].startswith("2\tb\n3\tc\n[lines 2-3 of 3]")
    assert out[4].split("\n")[0].endswith("/work/sub") and "g.txt" in out[4]     # cd persisted
    assert out[5] == "Submitted." and agent.messages[-1]["role"] == "tool"
    assert (tmp_path / "logs" / "trajectory.json").exists()
    assert context.n_input_tokens == 6000 and context.n_cache_tokens == 3600 and len(agent.calls) == 6


def test_the_assistant_message_is_echoed_whole_and_the_list_never_ends_on_it(make):
    signature = {"google": {"thought_signature": "sig-123"}}
    agent, _, _ = make([reply(("bash", {"command": "echo x"}), extra=signature), reply(("submit", {}))])
    second_request = agent.client.sent[1]
    assistant = [m for m in second_request if m["role"] == "assistant"][0]
    assert assistant["extra_content"] == signature
    assert all(request[-1]["role"] != "assistant" for request in agent.client.sent)


def test_a_turn_without_a_tool_call_gets_a_nudge(make):
    agent, _, _ = make([reply(text="thinking..."), reply(("submit", {}))])
    assert agent.messages[3] == {"role": "user", "content": NUDGE}


def test_a_malformed_tool_call_is_resent_and_counted(make):
    agent, _, _ = make([reply(finish="malformed_function_call"), reply(("submit", {}))])
    assert len(agent.client.sent) == 2 and agent.client.sent[0] == agent.client.sent[1]
    assert [c["finish_reason"] for c in agent.calls] == ["malformed_function_call", "tool_calls"]


def test_transient_errors_are_retried_but_gateway_refusals_stop_the_run(make):
    agent, _, _ = make([StatusError(503), reply(("submit", {}))])
    assert tool_messages(agent) == ["Submitted."]
    with pytest.raises(StatusError):
        make([StatusError(429, "run r reached 60 model calls")])


def test_long_outputs_are_capped_head_and_tail():
    text = "a" * 50 + "b" * 50
    assert cap(text, 100) == text
    capped = cap(text, 20)
    assert capped.startswith("a" * 10) and capped.endswith("b" * 10) and "80 characters omitted" in capped
