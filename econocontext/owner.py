"""EconoContext acting on a conversation it owns (our own agent, agents/react/agent.py).

Why it exists: the full-control track. With no hooks in the way, EconoContext sees every tool
call and result and may edit the conversation before every model call. It reuses the session
database (versions, items, decisions), the predictors (Jev or fixed guesses) and the prices.
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
"""

import json
import re
from pathlib import Path

import yaml

from .predictor import chunks
from .predictor.jev import Jev
from .predictor.prior import Prior
from .pricing import lifecycle
from .session import TESTS, Session, read_only_bash
from .tokens import count_tokens

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


class Owner:
    def __init__(self, run: str | None, mode: str, db_path: str | Path, force=(), cfg: dict | None = None,
                 cache: bool | None = None):
        cfg = cfg or yaml.safe_load(CONFIG.read_text())
        self.cfg, h = cfg, cfg["harness"]
        self.h = h
        self.cache = h.get("cache_in_decisions", True) if cache is None else cache
        self.p = lifecycle.from_card(h["price"]["provider"], h["price"]["model"], h["hit_share"],
                                     h["expected_output"], cache=self.cache)
        self.s = Session(db_path)
        self.predictor = Prior() if mode == "prior" else Jev(cfg["jev"]["model"], cfg["jev"]["timeout_seconds"],
                                                              cfg["jev"]["max_input_tokens"])
        self.run, self.force = run, set(force or [])
        self.item_of_call: dict[str, int] = {}   # tool_call_id -> item id
        self.calls, self.prompt = 0, h["fixed_prefix"]
        self._served = None

    def start(self, task: str) -> None:
        self.s.set("task", task)

    @property
    def calls_left(self) -> int:
        return max(3, self.h["calls_total"] - self.calls)

    def _predict(self, method: str, *args):
        """Ask the predictor; on failure use the fixed guesses and record why (fail open)."""
        try:
            answers, usage = getattr(self.predictor, method)(self.s.get("task") or "", *args)
            return answers, usage, self.predictor.name
        except Exception as exc:
            answers, _ = getattr(Prior(), method)(self.s.get("task") or "", *args)
            return answers, None, f"prior ({str(exc)[:120]})"

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
        prev = self.s.last_same("main", name, args)
        if not prev or prev["epoch"] != self.s.epoch or prev["output"] is None:
            return None
        in_context = self.s.full_copy_in_context("main", name, args)
        note = (NOTE + f"Same output as your earlier identical call ({describe(name, args)}); nothing was "
                "written since, so it is unchanged and still in the conversation above.") if in_context else None
        # The saved output is exactly what running it again would return, so it needs no note.
        served = prev["output"]
        options = {"run": lifecycle.keep(self.p, prev["tokens"], self.calls_left),
                   "serve": lifecycle.keep(self.p, count_tokens(served), self.calls_left)}
        if note:
            options["note"] = lifecycle.keep(self.p, count_tokens(note), self.calls_left)
        choice = min(options, key=lambda k: (options[k], k == "run"))  # a tie goes to not running it
        if choice == "run":
            return None
        self._served = (choice, prev["output"])
        self.s.log("1 dont_repeat" if choice == "note" else "3 serve_stored", choice, prev["id"],
                   prices=options, note=describe(name, args))
        return note if choice == "note" else served

    # Rules 9, 1, 2, 4: after a tool runs ----------------------------------------------------
    def after_tool(self, name: str, args: dict, output: str, served: bool, call_id: str | None = None) -> str:
        if served and self._served:
            form, full = self._served
            item = self.s.add_item("main", name, args, full, count_tokens(full),
                                   form="note" if form == "note" else "full", shown_tokens=count_tokens(output))
            self._bind(call_id, item)
            return output
        if name == "write_file" or (name == "bash" and not self.repeatable(name, args)):
            epoch = self.s.bump("epoch")
            self.s.log("9 invalidate", "bump", note=f"{describe(name, args)} -> epoch {epoch}")
        tokens = count_tokens(output)
        prev = self.s.full_copy_in_context("main", name, args) if self.repeatable(name, args) else None
        item = self.s.add_item("main", name, args, output, tokens)
        self._bind(call_id, item)
        if prev and prev["output"] == output and tokens > 50:    # rule 1 after a run (e.g. no epoch info)
            note = NOTE + f"Same output as your earlier identical call ({describe(name, args)}); still above."
            self.s.update_item(item, form="note", shown_tokens=count_tokens(note))
            self.s.log("1 dont_repeat", "note", item)
            return note
        if name == "read_file":
            trimmed = self._delta_read(args, output, item)
            if trimmed is not None:
                return trimmed
        if tokens >= self.h["big_tokens"]:
            return self._arrival(name, args, output, tokens, item)
        return output

    def _bind(self, call_id: str | None, item: int) -> None:
        if call_id:
            self.item_of_call[call_id] = item

    def _delta_read(self, args: dict, output: str, item: int) -> str | None:
        lines = output.split("\n")
        numbered = [(i, NUMBERED.match(line)) for i, line in enumerate(lines)]
        current = {int(m.group(1)): m.group(2) for _, m in numbered if m}
        if not current:
            return None
        seen: dict[int, str] = {}
        for prev in self.s.reads_of("main", args.get("path"), tool="read_file", key="path"):
            if prev["id"] == item or prev["form"] != "full" or prev["epoch"] != self.s.epoch:
                continue
            for line in (prev["output"] or "").split("\n"):
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
        self.s.update_item(item, form="slice", shown_tokens=count_tokens(text))
        self.s.log("2 delta_read", "trim", item, prices=lifecycle.note(self.p, count_tokens(text),
                                                                       count_tokens(output), self.calls_left))
        return text

    def _arrival(self, name: str, args: dict, text: str, tokens: int, item: int) -> str:
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
        shown = {form: count_tokens(t) for form, t in texts.items()}
        prices = lifecycle.arrival(self.p, tokens, shown, miss, self.calls_left, self.prompt,
                                   lifecycle.reread_at(answers.get("lifetime"), self.calls_left))
        forced = "4 arrival" in self.force
        choice = min(prices, key=prices.get)
        if forced and choice == "full":
            choice = "slice" if "slice" in texts else "preview"
        self.s.log("4 arrival", choice, item, forced=forced, prices=prices,
                   answers={"source": source, **answers}, jev_usage=usage)
        if choice == "full":
            return text
        self.s.update_item(item, form=choice, shown_tokens=shown[choice])
        what = "the parts most relevant to your task" if choice == "slice" else "the first and last lines"
        return (NOTE + f"This output has {len(lines)} lines (about {tokens} tokens); you are seeing {what}. "
                "The full output is saved: repeat the identical call to get all of it.\n" + texts[choice])

    # Rules 6 and 7: before a model call ---------------------------------------------------
    def _turns(self, messages: list[dict] | None = None) -> list[dict]:
        """The conversation as numbered turns for the predictor (cut from the front when long)."""
        out = []
        for i, m in enumerate(messages or self._messages):
            turn = {"turn": i, "role": m.get("role")}
            if m.get("content"):
                turn["text"] = m["content"] if isinstance(m["content"], str) else json.dumps(m["content"])
            if m.get("tool_calls"):
                turn["tool_calls"] = [{"name": c["function"]["name"], "arguments": c["function"].get("arguments")}
                                      for c in m["tool_calls"]]
            out.append(turn)
        total, kept = 0, []
        for turn in reversed(out):
            total += len(json.dumps(turn))
            if total > self.cfg["jev"]["max_state_chars"] and kept:
                break
            kept.append(turn)
        return list(reversed(kept))

    _messages: list[dict] = []

    async def before_call(self, messages: list[dict], calls: list[dict], complete) -> list[dict]:
        self._messages = messages
        self.calls = len(calls)
        self.s.set("calls", self.calls)
        usages = [c["usage"] for c in calls if c.get("usage")]
        if usages:
            self.prompt = usages[-1].get("prompt_tokens") or self.prompt
        if self.calls - self.s.get("last_check", -999) < self.h["check_every"]:
            return messages
        index = {m.get("tool_call_id"): i for i, m in enumerate(messages) if m.get("role") == "tool"}
        items = []
        for call_id, item_id in self.item_of_call.items():
            row = self.s.item(item_id)
            if row and row["in_context"] and row["shown_tokens"] >= self.h["big_tokens"] and call_id in index:
                items.append({"id": item_id, "turn": index[call_id], "tool": row["tool"],
                              "input": json.loads(row["input"]), "tokens": row["shown_tokens"]})
        may_compact = (self.prompt >= self.h["compact_min_prompt"]
                       and self.calls - self.s.get("last_compact", -999) >= self.h["compact_cooldown_calls"])
        forced_compact = "7 compact" in self.force and not self.s.get("compactions", 0) \
            and self.calls >= self.h["compact_cooldown_calls"]
        if not items and not may_compact and not forced_compact:
            return messages
        self.s.set("last_check", self.calls)
        boundaries = [i for i, m in enumerate(messages) if m.get("role") == "tool" and i >= 2
                      and (i + 1 == len(messages) or messages[i + 1].get("role") == "assistant")]
        answers, usage, source = self._predict("segment", self._turns(messages), items, boundaries)
        if (may_compact and answers["segment_done"] >= self.h["segment_done_min"]) or forced_compact:
            end = answers.get("segment_end") if answers.get("segment_end") in boundaries else \
                (boundaries[-1] if boundaries else None)
            if end is not None:
                await self._compact(messages, end, items, answers, usage, source, complete, forced_compact)
                return messages
        self._evict(messages, items, index, answers, usage, source)
        return messages

    def _evict(self, messages, items, index, answers, usage, source) -> None:
        forced = "6 evict" in self.force
        done = answers["done"]
        finished = [i for i in items if forced or done.get(i["id"], 0.0) >= self.h["evict_min_done"]]
        if not finished:
            self.s.log("6 evict", "none", answers={"source": source, **answers}, jev_usage=usage)
            return
        first = min(i["turn"] for i in finished)
        after = sum(count_tokens(json.dumps(m)) for m in messages[first + 1:])
        saved = sum(i["tokens"] for i in finished) * self.p.c * self.calls_left
        cache_break = after * (1 - self.p.cache_read) * self.p.hit_share
        risk = sum((1 - done.get(i["id"], 0.0)) * lifecycle.reread(self.p, self.prompt, i["tokens"],
                                                                   self.calls_left / 2) for i in finished)
        prices = {"keep": saved, "evict": cache_break + risk}
        go = forced or saved > cache_break + risk
        self.s.log("6 evict", "evict" if go else "keep", forced=forced, prices=prices,
                   answers={"source": source, **answers}, jev_usage=usage,
                   note=json.dumps([i["id"] for i in finished]))
        if not go:
            return
        for i in finished:
            message = messages[i["turn"]]
            message["content"] = (NOTE + f"The output of this call ({describe(i['tool'], i['input'])}) was removed "
                                  "from the conversation because it is no longer needed. It is saved: repeat the "
                                  "identical call to get it back.")
            self.s.update_item(i["id"], in_context=0, form="evicted", shown_tokens=count_tokens(message["content"]))

    async def _compact(self, messages, end, items, answers, usage, source, complete, forced) -> None:
        done = answers["done"]
        keep = [i for i in items if i["turn"] <= end and 1 - done.get(i["id"], 0.0) >= self.h["keep_verbatim_min"]]
        request = [m for m in messages[:end + 1]] + [{"role": "user", "content": SUMMARIZE}]
        reply = await complete(request)
        summary = (reply.choices[0].message.content or "").strip()
        if not summary:
            self.s.log("7 compact", "failed", forced=forced, answers={"source": source, **answers},
                       jev_usage=usage, note="the model wrote no summary")
            return
        kept_text = "\n\n".join(f"[kept word for word: {describe(i['tool'], i['input'])}]\n{messages[i['turn']]['content']}"
                                for i in keep)
        task = messages[1]["content"]
        dropped = [i["id"] for i in items if i["turn"] <= end and i not in keep]
        segment_ids = {self.item_of_call[c] for c in self.item_of_call
                       if any(m.get("tool_call_id") == c for m in messages[2:end + 1])}
        messages[1] = {"role": "user", "content": task + "\n\n" + NOTE + (
            f"Your earlier work (turns 2-{end}) was replaced by this summary you wrote:\n{summary}"
            + (f"\n\n{kept_text}" if kept_text else ""))}
        del messages[2:end + 1]
        for item_id in segment_ids - {i["id"] for i in keep}:
            self.s.update_item(item_id, in_context=0)
        out = reply.usage.model_dump() if reply.usage else {}
        written = (out.get("completion_tokens") or 0) + ((out.get("completion_tokens_details") or {}).get(
            "reasoning_tokens") or 0)
        prices = lifecycle.compaction(self.p, self.prompt, self.h["fixed_prefix"], count_tokens(summary),
                                      self.calls_left, [(1 - done.get(i["id"], 0.0), i["tokens"])
                                                        for i in items if i["id"] in dropped],
                                      summary_output=written)
        self.s.set("last_compact", self.calls)
        self.s.bump("compactions")
        self.s.log("7 compact", "compacted", forced=forced, prices=prices, answers={"source": source, **answers},
                   jev_usage=usage, note=json.dumps({"end_turn": end, "summary_tokens": count_tokens(summary),
                                                     "summary_call_output": written, "kept": [i["id"] for i in keep],
                                                     "removed_messages": end - 1}))

    def report(self) -> dict:
        rules: dict[str, int] = {}
        jev_calls = jev_in = 0
        for d in self.s.decisions():
            key = f"{d['rule']}: {d['action']}"
            rules[key] = rules.get(key, 0) + 1
            if d["jev_usage"]:
                jev_calls += 1
                jev_in += json.loads(d["jev_usage"]).get("input_tokens", 0)
        return {"decisions": rules, "jev": {"calls": jev_calls, "input_tokens": jev_in}}
