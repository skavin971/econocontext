"""EconoContext acting on a conversation it owns (our own agent, agents/react/agent.py).

Why it exists: the full-control track. With no hooks in the way, EconoContext sees every tool
call and result and may edit the conversation before every model call. It uses the agent DB (below),
the predictors (Jev or fixed guesses) and the prices.
Rules (plan numbering; 5 and 8 need workers, which this agent does not have):
  1 dont_repeat   identical call, nothing written since, earlier copy still in context -> short note
  2 delta_read    read_file overlapping lines already shown, unchanged                  -> only new lines
  3 serve_stored  identical read-only call, nothing written since, copy gone            -> saved output
  4 arrival       big result                                    -> full / Jev slice / preview (full saved)
  6 evict         big items Jev judges finished, if it pays     -> one-line marker (repeat the call to get it)
  7 compact       Jev judges a part of the work finished (segment_done >= 0.7), prompt >= 30k tokens,
                  8 calls since the last one -> the model summarizes the segment; the segment is removed
  9 invalidate    write_file, or bash that may write          -> stored results go stale (write epoch)
Every change is explained to the model with an [EconoContext] note (spike 1a).

Its state lives in the agent DB (agent_db, step 12a): every message is a segment, the window holds each
one's position and the form it is shown in with its exact token count, each tool result is stored in
full with the workspace version it read, and a write bumps that version. Jev's input is built only from
these reads plus the task. Run state (calls, checks, compactions) is plain variables here, and every
decision is a line of decisions.jsonl beside the trial's other logs.
"""

import json
import re
import time
from pathlib import Path

import yaml
from agent_db import Form, SegmentKind, make_segment

from measure.tokens import count_text

from .commands import TESTS, read_only_bash, tool_key
from .predictor import chunks
from .predictor.jev import Jev
from .predictor.prior import Prior
from .pricing import lifecycle

CONFIG = Path(__file__).resolve().parents[1] / "config" / "v2.yaml"
NOTE = "[EconoContext] "
SUMMARIZE = (
    "Stop working on the task for a moment. Write a summary of the part of the work shown above, to "
    "replace it in your context: what you found, what you changed (files, values, commands that "
    "worked), what you verified, and what remains. Be specific and brief. Reply with text only; do not "
    "call any tool.")
NUMBERED = re.compile(r"^(\d+)\t(.*)$")


def describe(name: str, args: dict) -> str:
    detail = args.get("command") or args.get("path") or json.dumps(args)
    return f"{name} {detail}"[:120]


def message_text(m: dict) -> str:
    content = m.get("content")
    return "" if not content else content if isinstance(content, str) else json.dumps(content)


class Owner:
    def __init__(self, run: str, mode: str, store, log_path: str | Path, force=(), cfg: dict | None = None,
                 cache: bool | None = None, model: str = "qwen3.8:27b", count=None):
        """store: an agent DB (agent_db.PostgresAgentDB); run: this run's id in it, used once.
        count: text -> tokens; the served model's own tokenizer (measure/tokens.py) unless a test gives one."""
        cfg = cfg or yaml.safe_load(CONFIG.read_text())
        self.cfg, h = cfg, cfg["harness"]
        self.h = h
        self.cache = h.get("cache_in_decisions", True) if cache is None else cache
        self.p = lifecycle.from_card(h["price"]["provider"], h["price"]["model"], h["hit_share"],
                                     h["expected_output"], cache=self.cache)
        self.store, self.log_path = store, Path(log_path)
        self.count = count or (lambda text: count_text(text, model))
        self.predictor = Prior() if mode == "prior" else Jev(cfg["jev"]["model"], cfg["jev"]["timeout_seconds"],
                                                              cfg["jev"]["max_input_tokens"])
        self.run, self.force = run, set(force or [])
        # run state
        self.task, self.calls, self.prompt = "", 0, h["fixed_prefix"]
        self.last_check = self.last_compact = -999
        self.compactions = self.items = 0
        self._served, self._result_id = None, None
        self._messages: list[dict] = []    # the harness's conversation (the same list), as of before_call
        self._pending: set[str] = set()    # tool results placed in the window, their message not yet seen

    def start(self, task: str) -> None:
        self.task = task
        self.store.start_run(self.run, task)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.touch()

    @property
    def calls_left(self) -> int:
        return max(3, self.h["calls_total"] - self.calls)

    def _predict(self, method: str, *args):
        """Ask the predictor; on failure use the fixed guesses and record why (fail open)."""
        try:
            answers, usage = getattr(self.predictor, method)(self.task, *args)
            return answers, usage, self.predictor.name
        except Exception as exc:
            answers, _ = getattr(Prior(), method)(self.task, *args)
            return answers, None, f"prior ({str(exc)[:120]})"

    # The agent DB: tool results, the window, decisions --------------------------------------
    @property
    def epoch(self) -> int:
        return int(self.store.current_versions(self.run).get("*", "0"))

    def _record(self, name: str, args: dict, output: str, call_id: str) -> int:
        """A tool result in full, with the workspace version it read; returns its item number."""
        self.items += 1
        result = make_segment(self.run, call_id, SegmentKind.TOOL_RESULT, output, self.count(output), role="tool",
                              pair_id=call_id)
        self.store.add_segment(result)
        self._result_id = result.id
        self.store.add_tool_result(str(self.items), self.run, name, tool_key(name, args), args, result.id,
                                   {"*": str(self.epoch)}, not self.repeatable(name, args))
        return self.items

    def _show(self, call_id: str, text: str, form: Form, tokens: int | None = None) -> None:
        """Place a tool result's shown text in the window, where its message will be (the harness appends it
        after this hook returns). tokens: the size the rules use for it, when not the whole text's (an
        arrival's preview or slice counts without its note line, as before step 12a)."""
        shown = make_segment(self.run, call_id, SegmentKind.TOOL_RESULT, text, self.count(text), role="tool",
                             pair_id=call_id)
        self.store.add_segment(shown)
        shown.position, shown.form, shown.window_tokens = len(self._messages), form, tokens
        self.store.set_window(self.run, [s for s in self.store.window(self.run) if s.pair_id != call_id or
                                         s.role != "tool" or s.part] + [shown])
        self._pending.add(call_id)

    def _segment(self, known: dict, native: str, kind, text: str, role: str, pair_id=None):
        candidate = make_segment(self.run, native, kind, text, 0, role=role, pair_id=pair_id)
        if candidate.id in known:
            return known[candidate.id]
        candidate.tokens = self.count(text)
        self.store.add_segment(candidate)
        return candidate

    def _sync(self, messages: list[dict], forms: dict[int, Form] | None = None, quote=()) -> None:
        """Make the window the conversation: each message a segment at its position (its tool calls as
        parts), in its form. forms: the form of a message the Owner just changed, by position. quote: tool
        results now quoted word for word inside message 1 (a compaction's kept items). Tool results placed
        by after_tool whose message the harness has not appended yet stay where they are."""
        forms = forms or {}
        current = self.store.window(self.run)
        known = {s.id: s for s in current}
        results = {s.pair_id: s for s in current if s.role == "tool" and not s.part}
        others = {s.position: s for s in current if s.role != "tool" and not s.part}
        window, seen = [], set()

        def place(s, position, part, form):
            s.position, s.part, s.form = position, part, form
            window.append(s)

        for i, m in enumerate(messages):
            text, role = message_text(m), m.get("role")
            if role == "tool":
                call_id = m.get("tool_call_id") or f"m{i}"
                seen.add(call_id)
                prev = results.get(call_id)
                if prev is not None and prev.text == text:
                    place(prev, i, 0, prev.form)
                else:   # changed by the Owner (forms), or capped by the harness: the window holds what is sent
                    s = self._segment(known, call_id, SegmentKind.TOOL_RESULT, text, "tool", call_id)
                    if prev is not None and i not in forms:   # capped: sized as before the cap, as before step 12a
                        s.window_tokens = prev.tokens if prev.window_tokens is None else prev.window_tokens
                    place(s, i, 0, forms.get(i) or (prev.form if prev is not None else Form.FULL))
                continue
            kind = SegmentKind.SYSTEM if role == "system" else \
                SegmentKind.TASK if i == 1 and text == self.task else SegmentKind.MESSAGE
            s = self._segment(known, f"m{i}", kind, text, role)
            prev = others.get(i)
            place(s, i, 0, prev.form if prev is not None and prev.id == s.id else forms.get(i, Form.FULL))
            for j, c in enumerate(m.get("tool_calls") or []):
                call = json.dumps({"name": c["function"]["name"], "arguments": c["function"].get("arguments")})
                place(self._segment(known, c.get("id") or f"m{i}.{j}", SegmentKind.TOOL_CALL, call, role, c.get("id")),
                      i, j + 1, Form.FULL)
        quoted = [s for s in current if s.role == "tool" and s.part] + [results[c] for c in quote if c in results]
        for k, s in enumerate(quoted):
            place(s, 1, k + 1, s.form)
        window += [results[c] for c in sorted(self._pending - seen) if c in results]
        self._pending -= seen
        self.store.set_window(self.run, window)

    def _results(self) -> list[dict]:
        """The tool results in the window, oldest first."""
        return self.store.window_results(self.run)

    def _full_copy(self, name: str, args: dict) -> dict | None:
        """The latest result of the identical call still shown word for word."""
        key = tool_key(name, args)
        rows = [r for r in self._results() if r["form"] == Form.FULL and r["tool_name"] == name and r["args_key"] == key]
        return rows[-1] if rows else None

    def _reads_of(self, path: str | None) -> list[dict]:
        """Earlier read_file results of this path still in the window, newest first."""
        return [r for r in reversed(self._results()) if path is not None and r["tool_name"] == "read_file"
                and r["args"].get("path") == path and r["form"] != Form.EVICTED]

    def _turns(self) -> list[dict]:
        """The conversation as numbered turns for the predictor, read from the window (cut from the front
        when long)."""
        out = []
        for s in self.store.window(self.run):
            if not s.part:
                out.append({"turn": s.position, "role": s.role, **({"text": s.text} if s.text else {})})
            elif s.kind == SegmentKind.TOOL_CALL:
                out[-1].setdefault("tool_calls", []).append(json.loads(s.text))
        total, kept = 0, []
        for turn in reversed(out):
            total += len(json.dumps(turn))
            if total > self.cfg["jev"]["max_state_chars"] and kept:
                break
            kept.append(turn)
        return list(reversed(kept))

    def log(self, rule: str, action: str, item_id: int | None = None, forced: bool = False,
            prices: dict | None = None, answers: dict | None = None, jev_usage: dict | None = None,
            note: str | None = None) -> None:
        with self.log_path.open("a") as f:
            f.write(json.dumps({"at": time.time(), "call_no": self.calls, "rule": rule, "item_id": item_id,
                                "action": action, "forced": bool(forced), "prices": prices or None,
                                "answers": answers or None, "jev_usage": jev_usage or None, "note": note}) + "\n")

    def decisions(self) -> list[dict]:
        return [json.loads(line) for line in self.log_path.read_text().splitlines() if line]

    # Rules 1 and 3: before a tool runs ---------------------------------------------------
    @staticmethod
    def repeatable(name: str, args: dict) -> bool:
        command = args.get("command", "")
        return name == "read_file" or (name == "bash" and (read_only_bash(command) or bool(TESTS.match(command))))

    def before_tool(self, name: str, args: dict) -> str | None:
        """An identical, unchanged call: priced among a short note, the saved output, or running it."""
        self._served = None
        if not self.repeatable(name, args):
            return None
        prev = self.store.find_tool_result(self.run, name, tool_key(name, args))   # valid: nothing written since
        if not prev:
            return None
        in_context = self._full_copy(name, args)
        note = (NOTE + f"Same output as your earlier identical call ({describe(name, args)}); nothing was "
                "written since, so it is unchanged and still in the conversation above.") if in_context else None
        # The saved output is exactly what running it again would return, so it needs no note.
        served = prev["text"]
        options = {"run": lifecycle.keep(self.p, prev["tokens"], self.calls_left),
                   "serve": lifecycle.keep(self.p, prev["tokens"], self.calls_left)}
        if note:
            options["note"] = lifecycle.keep(self.p, self.count(note), self.calls_left)
        choice = min(options, key=lambda k: (options[k], k == "run"))  # a tie goes to not running it
        if choice == "run":
            return None
        self._served = (choice, served)
        self.log("1 dont_repeat" if choice == "note" else "3 serve_stored", choice, int(prev["tool_result_id"]),
                 prices=options, note=describe(name, args))
        return note if choice == "note" else served

    # Rules 9, 1, 2, 4: after a tool runs ----------------------------------------------------
    def after_tool(self, name: str, args: dict, output: str, served: bool, call_id: str | None = None) -> str:
        call_id = call_id or f"item{self.items + 1}"
        if served and self._served:
            form, full = self._served
            self._record(name, args, full, call_id)
            self._show(call_id, output, Form.NOTE if form == "note" else Form.FULL)
            return output
        if name == "write_file" or (name == "bash" and not self.repeatable(name, args)):
            self.store.bump(self.run, [])
            self.log("9 invalidate", "bump", note=f"{describe(name, args)} -> epoch {self.epoch}")
        tokens = self.count(output)
        prev = self._full_copy(name, args) if self.repeatable(name, args) else None
        item = self._record(name, args, output, call_id)
        if prev and prev["full_text"] == output and tokens > 50:    # rule 1 after a run (e.g. no epoch info)
            note = NOTE + f"Same output as your earlier identical call ({describe(name, args)}); still above."
            self._show(call_id, note, Form.NOTE)
            self.log("1 dont_repeat", "note", item)
            return note
        shown, form, size = output, Form.FULL, None
        if name == "read_file":
            trimmed = self._delta_read(args, output, item)
            if trimmed is not None:
                shown, form = trimmed, Form.SLICE
        if form == Form.FULL and tokens >= self.h["big_tokens"]:
            shown, form, size = self._arrival(name, args, output, tokens, item)
        self._show(call_id, shown, form, size)
        return shown

    def _delta_read(self, args: dict, output: str, item: int) -> str | None:
        lines = output.split("\n")
        numbered = [(i, NUMBERED.match(line)) for i, line in enumerate(lines)]
        current = {int(m.group(1)): m.group(2) for _, m in numbered if m}
        if not current:
            return None
        seen: dict[int, str] = {}
        for prev in self._reads_of(args.get("path")):
            if int(prev["tool_result_id"]) == item or prev["form"] != Form.FULL or not prev["valid"]:
                continue
            for line in (prev["full_text"] or "").split("\n"):
                m = NUMBERED.match(line)
                if m:
                    seen.setdefault(int(m.group(1)), m.group(2))
        numbers = sorted(current)
        shown = [n for n in numbers if seen.get(n) == current[n]]
        if len(shown) < 5:
            return None
        if shown == numbers[:len(shown)]:
            keep = numbers[len(shown):]
        elif shown == numbers[-len(shown):]:
            keep = numbers[:len(numbers) - len(shown)]
        else:
            return None  # the overlap is in the middle: leave it
        footer = [line for line in lines if not NUMBERED.match(line)]   # "[lines a-b of N]", exit code
        body = [f"{n}\t{current[n]}" for n in keep]
        text = "\n".join([NOTE + f"Lines {shown[0]}-{shown[-1]} are unchanged and already above from your "
                          f"earlier read" + (f"; showing only lines {keep[0]}-{keep[-1]}." if keep else ".")]
                         + body + footer)
        self.log("2 delta_read", "trim", item, prices=lifecycle.note(self.p, self.count(text), self.count(output),
                                                                     self.calls_left))
        return text

    def _arrival(self, name: str, args: dict, text: str, tokens: int, item: int) -> tuple[str, Form, int | None]:
        self._sync(self._messages)
        text = self.store.segment(self._result_id).text     # what Jev sees comes from the agent DB
        lines = text.split("\n")
        spans = chunks(text, self.h["chunk_lines"])
        answers, usage, source = self._predict(
            "arrival", self._turns(), {"tool": name, "input": args, "text": text, "chunks": spans})
        p_need = answers["needed_again"]
        half = self.h["preview_lines"] // 2
        preview = "\n".join(lines[:half] + [f"... [{len(lines) - 2 * half} lines not shown] ..."] + lines[-half:])
        texts, miss = {"preview": preview}, {"preview": p_need}
        relevant = answers.get("relevant")
        if relevant:
            picked, mass = [], 0.0
            for i in sorted(relevant, key=relevant.get, reverse=True):
                if relevant[i] < self.h["slice_min_prob"] and picked:
                    break
                picked.append(i)
                mass += relevant[i]
                if mass >= 0.8:
                    break
            texts["slice"] = "\n".join(f"[lines {a}-{b}]\n" + "\n".join(lines[a - 1:b])
                                       for a, b in sorted(spans[i] for i in picked if i < len(spans)))
            miss["slice"] = p_need * (1 - mass)
        shown = {form: self.count(t) for form, t in texts.items()}
        prices = lifecycle.arrival(self.p, tokens, shown, miss, self.calls_left, self.prompt,
                                   lifecycle.reread_at(answers.get("lifetime"), self.calls_left))
        forced = "4 arrival" in self.force
        choice = min(prices, key=prices.get)
        if forced and choice == "full":
            choice = "slice" if "slice" in texts else "preview"
        self.log("4 arrival", choice, item, forced=forced, prices=prices,
                 answers={"source": source, **answers}, jev_usage=usage)
        if choice == "full":
            return text, Form.FULL, None
        what = "the parts most relevant to your task" if choice == "slice" else "the first and last lines"
        return (NOTE + f"This output has {len(lines)} lines (about {tokens} tokens); you are seeing {what}. "
                "The full output is saved: repeat the identical call to get all of it.\n" + texts[choice]), \
            Form.SLICE if choice == "slice" else Form.PREVIEW, shown[choice]

    # Rules 6 and 7: before a model call ---------------------------------------------------
    async def before_call(self, messages: list[dict], calls: list[dict], complete) -> list[dict]:
        self._messages = messages
        self.calls = len(calls)
        self._sync(messages)
        usages = [c["usage"] for c in calls if c.get("usage")]
        if usages:
            self.prompt = usages[-1].get("prompt_tokens") or self.prompt
        if self.calls - self.last_check < self.h["check_every"]:
            return messages
        items = [{"id": int(r["tool_result_id"]), "turn": r["position"], "tool": r["tool_name"], "input": r["args"],
                  "tokens": r["tokens"]} for r in self._results()
                 if not r["part"] and r["form"] != Form.EVICTED and r["tokens"] >= self.h["big_tokens"]
                 and r["position"] < len(messages) and messages[r["position"]].get("tool_call_id") == r["pair_id"]]
        may_compact = (self.prompt >= self.h["compact_min_prompt"]
                       and self.calls - self.last_compact >= self.h["compact_cooldown_calls"])
        forced_compact = "7 compact" in self.force and not self.compactions \
            and self.calls >= self.h["compact_cooldown_calls"]
        if not items and not may_compact and not forced_compact:
            return messages
        self.last_check = self.calls
        window = [s for s in self.store.window(self.run) if not s.part]
        roles = {s.position: s.role for s in window}
        boundaries = [p for p, role in roles.items() if role == "tool" and p >= 2
                      and (p + 1 == len(roles) or roles.get(p + 1) == "assistant")]
        answers, usage, source = self._predict("segment", self._turns(), items, boundaries)
        if (may_compact and answers["segment_done"] >= self.h["segment_done_min"]) or forced_compact:
            end = answers.get("segment_end") if answers.get("segment_end") in boundaries else \
                (boundaries[-1] if boundaries else None)
            if end is not None:
                await self._compact(messages, end, items, answers, usage, source, complete, forced_compact)
                return messages
        self._evict(messages, items, answers, usage, source)
        return messages

    def _evict(self, messages, items, answers, usage, source) -> None:
        forced = "6 evict" in self.force
        done = answers["done"]
        finished = [i for i in items if forced or done.get(i["id"], 0.0) >= self.h["evict_min_done"]]
        if not finished:
            self.log("6 evict", "none", answers={"source": source, **answers}, jev_usage=usage)
            return
        first = min(i["turn"] for i in finished)
        after = sum(self.count(json.dumps(m)) for m in messages[first + 1:])
        saved = sum(i["tokens"] for i in finished) * self.p.c * self.calls_left
        cache_break = after * (1 - self.p.cache_read) * self.p.hit_share
        risk = sum((1 - done.get(i["id"], 0.0)) * lifecycle.reread(self.p, self.prompt, i["tokens"],
                                                                   self.calls_left / 2) for i in finished)
        prices = {"keep": saved, "evict": cache_break + risk}
        go = forced or saved > cache_break + risk
        self.log("6 evict", "evict" if go else "keep", forced=forced, prices=prices,
                 answers={"source": source, **answers}, jev_usage=usage,
                 note=json.dumps([i["id"] for i in finished]))
        if not go:
            return
        for i in finished:
            messages[i["turn"]]["content"] = (
                NOTE + f"The output of this call ({describe(i['tool'], i['input'])}) was removed from the "
                "conversation because it is no longer needed. It is saved: repeat the identical call to get it back.")
        self._sync(messages, forms={i["turn"]: Form.EVICTED for i in finished})

    async def _compact(self, messages, end, items, answers, usage, source, complete, forced) -> None:
        done = answers["done"]
        keep = [i for i in items if i["turn"] <= end and 1 - done.get(i["id"], 0.0) >= self.h["keep_verbatim_min"]]
        request = [m for m in messages[:end + 1]] + [{"role": "user", "content": SUMMARIZE}]
        reply = await complete(request)
        summary = (reply.choices[0].message.content or "").strip()
        if not summary:
            self.log("7 compact", "failed", forced=forced, answers={"source": source, **answers},
                     jev_usage=usage, note="the model wrote no summary")
            return
        kept_text = "\n\n".join(f"[kept word for word: {describe(i['tool'], i['input'])}]\n{messages[i['turn']]['content']}"
                                for i in keep)
        kept_calls = [messages[i["turn"]].get("tool_call_id") for i in keep]
        task = messages[1]["content"]
        dropped = [i["id"] for i in items if i["turn"] <= end and i not in keep]
        messages[1] = {"role": "user", "content": task + "\n\n" + NOTE + (
            f"Your earlier work (turns 2-{end}) was replaced by this summary you wrote:\n{summary}"
            + (f"\n\n{kept_text}" if kept_text else ""))}
        del messages[2:end + 1]
        self._sync(messages, forms={1: Form.SUMMARY}, quote=kept_calls)
        out = reply.usage.model_dump() if reply.usage else {}
        written = (out.get("completion_tokens") or 0) + ((out.get("completion_tokens_details") or {}).get(
            "reasoning_tokens") or 0)
        summary_tokens = self.count(summary)
        prices = lifecycle.compaction(self.p, self.prompt, self.h["fixed_prefix"], summary_tokens,
                                      self.calls_left, [(1 - done.get(i["id"], 0.0), i["tokens"])
                                                        for i in items if i["id"] in dropped],
                                      summary_output=written)
        self.last_compact = self.calls
        self.compactions += 1
        self.log("7 compact", "compacted", forced=forced, prices=prices, answers={"source": source, **answers},
                 jev_usage=usage, note=json.dumps({"end_turn": end, "summary_tokens": summary_tokens,
                                                   "summary_call_output": written, "kept": [i["id"] for i in keep],
                                                   "removed_messages": end - 1}))

    def report(self) -> dict:
        """At the end of the run (the harness saves its trajectory): the window gets the final messages."""
        if self._messages:
            self._sync(self._messages)
        rules: dict[str, int] = {}
        jev_calls = jev_in = 0
        for d in self.decisions():
            key = f"{d['rule']}: {d['action']}"
            rules[key] = rules.get(key, 0) + 1
            if d["jev_usage"]:
                jev_calls += 1
                jev_in += d["jev_usage"].get("input_tokens", 0)
        return {"decisions": rules, "jev": {"calls": jev_calls, "input_tokens": jev_in}}
