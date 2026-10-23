"""Private artifact IO, frozen settings, token counting, and gateway transport."""

import copy
import hashlib
import json
import re
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[3]
DEFAULTS = {
    "model": "qwen3.8:27b", "provider": "purdue", "chunk_tokens": 400,
    "page_tokens": 2000, "max_topics": 3, "repeats": 3, "context_tokens": 65536,
    "max_calls": 64, "invalid_limit": 3, "score_attempts": 3,
    "jev_model": "jev-latest", "jev_max_input_tokens": 28000,
    "sampling": {"temperature": 0.7, "top_p": 0.95, "max_tokens": 4096,
                 "reasoning_effort": "medium", "chat_template_kwargs": {"enable_thinking": True}},
    "judge": {"model": "qwen3:32b", "temperature": 0.7, "top_p": 0.8,
              "top_k": 20, "max_tokens": 4096},
}


def digest(value):
    data = value if isinstance(value, str) else json.dumps(value, sort_keys=True, allow_nan=False)
    return hashlib.sha256(data.encode()).hexdigest()


def settings(overrides=None):
    cfg = copy.deepcopy(DEFAULTS)
    overrides = overrides or {}
    if set(overrides) - set(cfg):
        raise ValueError("unknown experiment setting")
    for key, value in overrides.items():
        if key in {"sampling", "judge"}:
            if not isinstance(value, dict):
                raise ValueError(f"{key} must be a settings object")
            cfg[key].update(value)
        else:
            cfg[key] = value
    for key in ("chunk_tokens", "page_tokens", "max_topics", "repeats", "context_tokens",
                "max_calls", "invalid_limit", "score_attempts", "jev_max_input_tokens"):
        if type(cfg[key]) is not int or cfg[key] < 1:
            raise ValueError(f"{key} must be a positive integer")
    if cfg["max_topics"] > 3 or cfg["chunk_tokens"] >= cfg["page_tokens"]:
        raise ValueError("at most three topics; chunks must be smaller than pages")
    if cfg["provider"] not in {"purdue", "vllm"}:
        raise ValueError("the measurement tokenizer supports purdue or vllm for this pilot")
    if not isinstance(cfg["sampling"], dict) or type(cfg["sampling"].get("max_tokens")) is not int or cfg["sampling"]["max_tokens"] < 1:
        raise ValueError("sampling must include a positive max_tokens limit")
    return cfg


def private_path(path):
    path = Path(path).expanduser().resolve()
    if path == ROOT or ROOT in path.parents:
        raise ValueError("benchmark artifacts must be outside the repository")
    return path


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    """Create immutable stage artifacts; reruns must match or use a new directory."""
    path = Path(path)
    if path.exists():
        if read(path) != value:
            raise ValueError(f"artifact already exists with different content: {path.name}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")


def rows(path):
    path = Path(path)
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def append(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")


def write_rows(path, values):
    path = Path(path)
    if path.exists():
        if rows(path) != values:
            raise ValueError(f"artifact already exists with different content: {path.name}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        for value in values:
            stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")


def load_experiment(folder):
    folder = private_path(folder)
    manifest, fixture = read(folder / "manifest.json"), read(folder / "fixture.json")
    if manifest["fixture_hash"] != digest(fixture):
        raise ValueError("fixture changed after preparation")
    if manifest["config_hash"] != digest(manifest["config"]):
        raise ValueError("frozen configuration changed")
    return folder, manifest, fixture


class Tokens:
    def __init__(self, cfg):
        from measure.models import load
        from measure.tokens import resolve, tokenizer
        self.cfg = cfg
        hf_id = resolve(cfg["model"])
        self.tok = tokenizer(hf_id, server=cfg["provider"])
        self.provenance = {"hf_id": hf_id, "revision": load(hf_id)["revision"],
                           "provider": cfg["provider"]}

    def count(self, text):
        return len(self.tok.encode(text, add_special_tokens=False))

    def coverage(self, text, spans):
        """Count original token positions touched by exposed character intervals."""
        offsets = self.tok(text, add_special_tokens=False, return_offsets_mapping=True)["offset_mapping"]
        return sum(any(start < b and a < end for a, b in spans) for start, end in offsets)

    def prompt(self, messages, tools):
        from measure.flops import prompt_ids
        return len(prompt_ids(self.tok, {"messages": messages, "tools": tools,
                                        **self.cfg["sampling"]}))


class GatewayError(RuntimeError):
    """Sanitized transport errors; provider text is retained only in gateway logs."""


class Gateway:
    def __init__(self, url, run_id, artifact_dir):
        if not re.fullmatch(r"bcp[A-Za-z0-9._+-]*", run_id):
            raise ValueError("run IDs must start with bcp and use letters, numbers, . _ + -")
        self.url, self.run_id = url.rstrip("/"), run_id
        self.artifact_dir = private_path(artifact_dir)

    def preflight(self, cfg):
        with urlopen(self.url + "/health", timeout=10) as reply:
            health = json.load(reply)
        if health.get("provider") != cfg["provider"]:
            raise ValueError("gateway provider differs from frozen configuration")
        log_dir = next((p for prefix, p in health.get("log_dir_for", {}).items()
                        if self.run_id.startswith(prefix)), health.get("log_dir"))
        if not log_dir or not Path(log_dir).expanduser().is_absolute():
            raise ValueError("gateway must advertise an absolute private log directory")
        private_path(log_dir)
        cap = next((v for k, v in health.get("max_tokens_for", {}).items()
                    if self.run_id.startswith(k)), health.get("max_tokens"))
        if cap != cfg["sampling"]["max_tokens"]:
            raise ValueError("gateway output cap differs from frozen sampling")
        from gateway.server import PROVIDERS
        pinned = {**PROVIDERS[cfg["provider"]].pins, "max_tokens": cap,
                  "chat_template_kwargs": PROVIDERS[cfg["provider"]].template}
        for key, value in cfg["sampling"].items():
            if key in pinned and pinned[key] != value:
                raise ValueError(f"gateway pins disagree with sampling: {key}")
        write(self.artifact_dir / "gateway.json", {
            "provider": health["provider"], "upstream": health["upstream"],
            "log_directory": str(Path(log_dir).expanduser()), "max_tokens": cap,
        })

    def call(self, messages, sampling, *, tools=None, kind="agent"):
        body = {**sampling, "messages": messages}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "required"
        request = Request(f"{self.url}/run/{self.run_id}/v1/chat/completions",
                          data=json.dumps(body).encode(), headers={"Content-Type": "application/json",
                          "X-Econo-Call-Kind": kind}, method="POST")
        started = time.monotonic()
        try:
            with urlopen(request, timeout=900) as reply:
                response = json.load(reply)
        except HTTPError as exc:
            append(self.artifact_dir / "calls.jsonl", {"run_id": self.run_id, "kind": kind,
                   "request": body, "response": {}, "error": f"HTTP {exc.code}",
                   "latency_ms": (time.monotonic() - started) * 1000})
            raise GatewayError(f"gateway HTTP {exc.code}") from None
        except (URLError, TimeoutError, ValueError):
            append(self.artifact_dir / "calls.jsonl", {"run_id": self.run_id, "kind": kind,
                   "request": body, "response": {}, "error": "transport_error",
                   "latency_ms": (time.monotonic() - started) * 1000})
            raise GatewayError("gateway failed or returned invalid JSON") from None
        append(self.artifact_dir / "calls.jsonl", {
            "run_id": self.run_id, "kind": kind, "request": body,
            "response": response, "latency_ms": (time.monotonic() - started) * 1000,
        })
        try:
            message = response["choices"][0]["message"]
        except (KeyError, TypeError, IndexError):
            raise GatewayError("gateway returned no message") from None
        if not isinstance(message, dict):
            raise GatewayError("gateway returned an invalid message")
        return response


def content(response):
    text = response["choices"][0]["message"].get("content") or ""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


def usage_totals(calls):
    """Unknown usage stays unknown, even when other calls have valid counters."""
    result = {}
    for source, dest in (("prompt_tokens", "total_model_input_tokens"),
                         ("completion_tokens", "total_model_output_tokens")):
        values = [(c.get("response", {}).get("usage") or {}).get(source) for c in calls]
        known = [v for v in values if type(v) is int and v >= 0]
        result[dest] = sum(known) if len(known) == len(values) else None
        result["known_" + dest] = sum(known)
    result["usage_complete"] = all(result[k] is not None for k in
                                     ("total_model_input_tokens", "total_model_output_tokens"))
    reasoning = [((c.get("response", {}).get("usage") or {}).get("completion_tokens_details") or {}).get("reasoning_tokens")
                 for c in calls]
    result["reasoning_tokens"] = (sum(reasoning) if reasoning and all(type(v) is int and v >= 0 for v in reasoning)
                                  else None)
    return result
