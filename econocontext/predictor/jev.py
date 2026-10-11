"""Jev (TypeSafe's classifier) as a predictor: one HTTP request per moment, several questions each.

API (https://docs.typesafe.ai/api.md, checked 2026-10-05): POST /v1/systemone with
{"model", "state", "questions": {key: {"type": "noul"|"choice", "instructions", "criteria"?}}};
answers come back under the same keys: noul -> {"noul": p}; choice -> {"choice", "probabilities",
"confidence"}. The key comes only from TYPESAFE_API_KEY. Standard library only.
"""

import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import LIFETIMES

URL = "https://api.typesafe.ai/v1/systemone"
# Jev's own tokens are smaller than chars/4: real states measured 2.55-2.67 characters per Jev token
# (2026-10-10, runs/2026-10-10-jev400/), and the 2026-10-06 probe accepted 60,000 characters (30,290
# Jev tokens) and refused 100,000. At 2.5, tc9 still had 1 fallback (max_tokens_exceeded) in 69
# questions; 2.3 (Kavin, step 10) keeps a wider margin.
CHARS_PER_JEV_TOKEN = 2.3
ERROR_TYPE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")   # an identifier like max_tokens_exceeded, never content


def numbered(text: str) -> str:
    return "\n".join(f"{i}\t{line}" for i, line in enumerate(text.splitlines(), 1))


def error_type(exc: HTTPError) -> str | None:
    """Jev's error type from an error reply ({"detail": {"error_type": ...}}), only if it is an identifier."""
    try:
        kind = (json.loads(exc.read() or b"{}").get("detail") or {}).get("error_type")
    except (ValueError, AttributeError, OSError):
        return None
    return kind if isinstance(kind, str) and ERROR_TYPE.match(kind) else None


def shorten_lines(text: str, room: int) -> str:
    """The head and tail lines of `text` that fit in `room` characters, with a note of what was cut."""
    lines, head, tail, used = text.split("\n"), [], [], 0
    room -= 40                                           # the note
    while len(head) + len(tail) < len(lines):
        line = lines[len(head)] if len(head) <= len(tail) else lines[-1 - len(tail)]
        if used + len(line) + 1 > room:
            break
        (head if len(head) <= len(tail) else tail).append(line)
        used += len(line) + 1
    cut = len(lines) - len(head) - len(tail)
    return "\n".join(head + [f"... [{cut} lines cut to fit] ..."] + tail[::-1]) if cut else text


def shorten_text(text: str, room: int) -> str:
    """The head and tail of `text` within `room` characters, with a note of what was cut."""
    room = max(0, room - 50)                              # the note is at most 41 characters
    if len(text) <= room:
        return text
    half = room // 2
    return f"{text[:half]} ... [{len(text) - room} characters cut to fit] ... {text[len(text) - (room - half):]}"


class Jev:
    name = "jev"

    def __init__(self, model: str = "jev-latest", timeout: float = 20, max_input_tokens: int = 28_000):
        self.model, self.timeout, self.max_input_tokens = model, timeout, max_input_tokens

    def fit(self, state: dict, questions: dict | None = None) -> dict:
        """Keep the request under Jev's input limit, budgeted in Jev's own units: max_input_tokens Jev
        tokens of CHARS_PER_JEV_TOKEN characters each (28,000 -> 64,400 characters). The whole request
        counts: the state and the questions. Cuts, in order, until it fits:
          1. the conversation from the front, keeping the last two turns (recent turns matter most);
          2. a long new tool result to its head and tail lines (lines cut cannot be chosen as relevant,
             so a slice never contains them);
          3. the remaining turns' texts and tool-call arguments, oldest first, to their head and tail."""
        limit = round(self.max_input_tokens * CHARS_PER_JEV_TOKEN)   # int() would give 64,399
        extra = len(json.dumps(questions)) if questions else 0

        def size() -> int:
            return len(json.dumps(state)) + extra
        convo = state.get("conversation")
        while isinstance(convo, list) and len(convo) > 2 and size() > limit:
            del convo[0]
        result = state.get("new_tool_result")
        if isinstance(result, dict) and isinstance(result.get("lines"), str) and size() > limit:
            result["lines"] = shorten_lines(result["lines"], len(result["lines"]) - (size() - limit))
        for turn in convo if isinstance(convo, list) else []:
            if not isinstance(turn, dict):
                continue
            for holder, key in [(turn, "text")] + [(call, "arguments") for call in turn.get("tool_calls") or []
                                                   if isinstance(call, dict)]:
                if size() > limit and isinstance(holder.get(key), str):
                    holder[key] = shorten_text(holder[key], len(holder[key]) - (size() - limit))
        return state

    def ask(self, state: dict, questions: dict) -> tuple[dict, dict]:
        key = os.environ.get("TYPESAFE_API_KEY")
        if not key:
            raise RuntimeError("TYPESAFE_API_KEY is not set")
        state = self.fit(state, questions)
        body = json.dumps({"model": self.model, "state": state, "questions": questions},
                          allow_nan=False).encode()
        request = Request(URL, data=body, method="POST", headers={
            "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                reply = json.load(response)
        except HTTPError as exc:  # never the key, never content: only Jev's error type, if it is one
            raise RuntimeError(f"Jev HTTP {exc.code}" + (f" {kind}" if (kind := error_type(exc)) else "")) from None
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
