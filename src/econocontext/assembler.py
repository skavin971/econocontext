"""Exact per-request assembly; this module never chooses evidence."""

import math
import time

from .contracts import Assembled, CandidatePlan, FeasibilityError, Worker, canonical, digest
from .state import ExecutionState

RENDERER = "stable-v1"


def token_count(value, margin=1.2):
    # UTF-8 byte heuristic, explicitly not provider tokenization.
    return math.ceil(len(canonical(value).encode()) / 3 * margin)


def validate_protocol(messages):
    pending = set()
    for message in messages:
        role = message["role"]
        if role == "tool":
            tool_id = message.get("tool_call_id")
            if tool_id not in pending:
                raise FeasibilityError("Unmatched tool response")
            pending.remove(tool_id)
        else:
            if pending:
                raise FeasibilityError("Unanswered tool calls before next message")
            for call in message.get("tool_calls", []):
                if call["id"] in pending:
                    raise FeasibilityError("Duplicate tool-call ID")
                pending.add(call["id"])
    if pending:
        raise FeasibilityError("Cannot submit request with pending tool calls")


def evidence_message(evidence, text):
    return {
        "role": "user",
        "content": f"Evidence {evidence['source']} version {evidence['version']} ref {evidence['id']}\n{text}",
    }


class Assembler:
    def __init__(self, memory, config):
        self.memory, self.config = memory, config

    async def assemble(
        self, worker: Worker, plan: CandidatePlan | None, state: ExecutionState
    ) -> Assembled:
        start = time.monotonic()
        history = (
            state.get("history") if "history" in state else await self.memory.history(worker.id)
        )
        history = [dict(m) for m in history]
        messages = [
            dict(role="system", content=state["adapter"].prompt(worker, state["method"]))
        ] + history
        refs = []
        # Selected evidence is appended at assignment, not reinserted or reordered every turn.
        selected = plan.evidence if plan else []
        for ref in selected:
            evidence = await self.memory.get(ref, worker.run_id)
            refs.append(
                dict(
                    id=ref,
                    source=evidence["source"],
                    version=evidence["version"],
                    payload=evidence["payload"],
                    excerpt=evidence["excerpt"],
                )
            )
        # Include bindings from earlier assignments for exact source attribution.
        for message in history:
            for ref in message.pop("_evidence", []):
                evidence = await self.memory.get(ref, worker.run_id)
                if not any(r["id"] == ref for r in refs):
                    refs.append(
                        {k: evidence[k] for k in ("id", "source", "version", "payload", "excerpt")}
                    )
        tools = state["adapter"].tools(worker, state["method"])
        validate_protocol(messages)
        request = dict(
            model=self.config.model,
            messages=messages,
            tools=tools,
            **{self.config.output_parameter: state["limits"].output_tokens},
        )
        tokens = token_count(request, state["limits"].safety_margin)
        capacity = min(
            state["limits"].context_tokens,
            self.config.live_context_tokens or state["limits"].context_tokens,
        )
        if tokens + state["limits"].output_tokens > capacity:
            raise FeasibilityError(
                f"Context capacity exceeded: {tokens} input + {state['limits'].output_tokens} reserved > {capacity}"
            )
        manifest = dict(
            renderer=RENDERER,
            worker_id=worker.id,
            revision=worker.revision,
            evidence=refs,
            request=self.memory.artifacts.json(request),
            tokenizer="utf8-bytes/3 estimate",
            safety_margin=state["limits"].safety_margin,
            tokens=tokens,
            config=self.config.fingerprint(),
        )
        ref = self.memory.artifacts.json(manifest)
        if state.get("preview"):
            return Assembled(
                messages=messages,
                tools=tools,
                tokens=tokens,
                manifest=ref,
                fingerprint=digest(request),
                request=request,
            )
        worker.context_manifest = ref
        await self.memory.save("worker", worker, worker.scope)
        await self.memory.event(
            worker.run_id,
            "assembly",
            dict(
                worker_id=worker.id,
                plan_id=plan.id if plan else None,
                manifest=ref,
                tokens=tokens,
                duration=time.monotonic() - start,
            ),
        )
        return Assembled(
            messages=messages,
            tools=tools,
            tokens=tokens,
            manifest=ref,
            fingerprint=digest(request),
            request=request,
        )

    def reconstruct(self, manifest_ref):
        manifest = self.memory.artifacts.read_json(manifest_ref)
        return self.memory.artifacts.read_json(manifest["request"])

    async def additions(self, plan, state):
        messages = []
        for ref in plan.evidence:
            evidence = await self.memory.get(ref, state["run_id"])
            text = self.memory.artifacts.get(evidence["payload"]).decode()
            msg = evidence_message(evidence, text)
            msg["_evidence"] = [ref]
            messages.append(msg)
        return messages
