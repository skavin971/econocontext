"""Jev (TypeSafe's classifier) as a predictor: one HTTP request per moment, several questions each.

API (https://docs.typesafe.ai/api.md, checked 2026-10-05): POST /v1/systemone with
{"model", "state", "questions": {key: {"type": "noul"|"choice", "instructions", "criteria"?}}};
answers come back under the same keys: noul -> {"noul": p}; choice -> {"choice", "probabilities",
"confidence"}. The key comes only from TYPESAFE_API_KEY. Standard library only.
"""

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..tokens import count_tokens
from . import LIFETIMES, PHASES

URL = "https://api.typesafe.ai/v1/systemone"


def numbered(text: str) -> str:
    return "\n".join(f"{i}\t{line}" for i, line in enumerate(text.splitlines(), 1))


class Jev:
    name = "jev"

    def __init__(self, model: str = "jev-latest", timeout: float = 20, max_input_tokens: int = 28_000):
        self.model, self.timeout, self.max_input_tokens = model, timeout, max_input_tokens

    def fit(self, state: dict) -> dict:
        """Keep the request under Jev's input limit (probe 2026-10-06: about 30,290 input tokens was
        accepted, about 50k was refused with max_tokens_exceeded). Cut the conversation from the front
        first (recent turns matter most), then shorten a long tool result to its head and tail. Lines
        cut from a tool result cannot be chosen as relevant, so a slice never contains them."""
        def size() -> int:
            return count_tokens(json.dumps(state))
        convo = state.get("conversation")
        while isinstance(convo, list) and len(convo) > 2 and size() > self.max_input_tokens:
            del convo[0]
        result = state.get("new_tool_result")
        if isinstance(result, dict) and isinstance(result.get("lines"), str) and size() > self.max_input_tokens:
            lines = result["lines"].split("\n")
            excess = size() - self.max_input_tokens
            keep = max(20, len(lines) - int(len(lines) * excess / max(1, count_tokens(result["lines"]))) - 10)
            head = keep // 2
            result["lines"] = "\n".join(lines[:head] + [f"... [{len(lines) - keep} lines cut to fit] ..."]
                                        + lines[-(keep - head):])
        return state

    def ask(self, state: dict, questions: dict) -> tuple[dict, dict]:
        key = os.environ.get("TYPESAFE_API_KEY")
        if not key:
            raise RuntimeError("TYPESAFE_API_KEY is not set")
        state = self.fit(state)
        body = json.dumps({"model": self.model, "state": state, "questions": questions},
                          allow_nan=False).encode()
        request = Request(URL, data=body, method="POST", headers={
            "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                reply = json.load(response)
        except HTTPError as exc:  # never include response bodies or the key in errors
            raise RuntimeError(f"Jev HTTP {exc.code}") from None
        except (URLError, TimeoutError):
            raise RuntimeError("Jev request failed or timed out") from None
        answers = reply.get("answers")
        if not isinstance(answers, dict):
            raise ValueError("Jev returned no answers")
        return answers, reply.get("usage") or {}

    @staticmethod
    def _p(answer: dict) -> float:
        p = answer.get("noul")
        if type(p) not in (int, float) or not 0 <= p <= 1:
            raise ValueError("Jev returned an invalid probability")
        return float(p)

    def arrival(self, task, convo, item):
        state = {"task": task, "conversation": convo,
                 "new_tool_result": {"tool": item["tool"], "input": item["input"],
                                     "lines": numbered(item["text"])}}
        questions = {
            "needed_again": {"type": "noul", "instructions": (
                "The agent just received `new_tool_result`. After its next step, will it need "
                "information from this result again to finish its task?")},
            "lifetime": {"type": "choice", "criteria": LIFETIMES, "instructions": (
                "For how long will the agent keep using `new_tool_result`?")},
        }
        spans = item.get("chunks") or []
        if len(spans) > 1:
            questions["relevant"] = {"type": "choice", "instructions": (
                "Which part of `new_tool_result` (by line numbers) holds what the agent needs "
                "for its task?"), "criteria": {f"c{i}": f"lines {a}-{b}" for i, (a, b) in enumerate(spans)}}
        answers, usage = self.ask(state, questions)
        out = {"needed_again": self._p(answers["needed_again"]),
               "lifetime": answers["lifetime"].get("choice"), "relevant": None}
        if "relevant" in answers:
            probs = answers["relevant"].get("probabilities") or {}
            out["relevant"] = {int(k[1:]): float(v) for k, v in probs.items() if k[1:].isdigit()}
        return out, usage

    def live(self, task, convo, items):
        state = {"task": task, "conversation": convo,
                 "items_in_context": [{"id": i["id"], "tool": i["tool"], "input": i["input"],
                                       "tokens": i["tokens"]} for i in items]}
        questions = {f"done_{i['id']}": {"type": "noul", "instructions": (
            f"Is the agent finished using the result of item {i['id']} in `items_in_context`, "
            "so it will not need it for the rest of the task?")} for i in items}
        questions["phase"] = {"type": "choice", "criteria": PHASES,
                              "instructions": "Which phase of the task is the agent in now?"}
        questions["portion_done"] = {"type": "noul", "instructions": (
            "Has the agent just finished a distinct part of its task (for example, it found the "
            "cause and is moving on to fixing it), so earlier work could be summarized?")}
        answers, usage = self.ask(state, questions)
        return {"done": {i["id"]: self._p(answers[f"done_{i['id']}"]) for i in items},
                "phase": answers["phase"].get("choice"),
                "portion_done": self._p(answers["portion_done"])}, usage

    def subtask(self, task, convo, prompt, items):
        state = {"task": task, "conversation": convo, "new_subtask": prompt,
                 "worker_items": [{"id": i["id"], "tool": i["tool"], "input": i["input"]} for i in items]}
        questions = {f"needed_{i['id']}": {"type": "noul", "instructions": (
            f"Will the new sub-task `new_subtask` need the information in worker item {i['id']}?")}
            for i in items}
        answers, usage = self.ask(state, questions)
        return {"needed": {i["id"]: self._p(answers[f"needed_{i['id']}"]) for i in items}}, usage

    def segment(self, task, turns, items, boundaries):
        """Rules 6 and 7 in a harness that owns its context. `turns` = the conversation as numbered
        turns; `items` = big tool outputs still in context; `boundaries` = turn numbers where a
        finished segment could end (right after a tool result)."""
        state = {"task": task, "conversation": turns,
                 "items_in_context": [{"id": i["id"], "turn": i.get("turn"), "tool": i["tool"],
                                       "input": i["input"], "tokens": i["tokens"]} for i in items]}
        questions = {f"done_{i['id']}": {"type": "noul", "instructions": (
            f"Is the agent finished using the output of item {i['id']} (turn {i.get('turn')}), so it will not "
            "need that exact output again for the rest of the task?")} for i in items}
        questions["segment_done"] = {"type": "noul", "instructions": SEGMENT_DONE}
        if len(boundaries) > 1:
            questions["segment_end"] = {"type": "choice", "instructions": SEGMENT_END,
                                        "criteria": {f"t{b}": f"after turn {b}" for b in boundaries[-60:]}}
        answers, usage = self.ask(state, questions)
        end = None
        if "segment_end" in answers and answers["segment_end"].get("choice"):
            end = int(answers["segment_end"]["choice"][1:])
        elif boundaries:
            end = boundaries[-1]
        return {"done": {i["id"]: self._p(answers[f"done_{i['id']}"]) for i in items},
                "segment_done": self._p(answers["segment_done"]), "segment_end": end}, usage


SEGMENT_DONE = (
    "Looking at the agent's task and its conversation so far, has the agent fully finished a "
    "self-contained part of the work (a segment), so that the detailed tool outputs and reasoning from "
    "that part are no longer needed word for word, only a short record of its results? Example: the task "
    "needs 10 SQL queries and query 1 is written, run and checked; the agent is starting query 2. Answer "
    "yes only if (a) that part's goal was reached or abandoned, (b) the agent has moved on or is about to "
    "move on to a different part, and (c) what the rest of the task needs from it fits in a few lines "
    "(file names, values, decisions, what was verified). Answer no if the agent is in the middle of a "
    "sub-task, debugging, or is likely to need exact earlier output (code, logs, data rows) soon.")
SEGMENT_END = (
    "Which turn ends the finished segment? The segment runs from the start of the conversation (after "
    "the task) up to and including that turn; everything after it is still in progress.")
