"""ViewContextEnv: CLM's ContextEnv with VIEW.md as the only context mechanism.

CLM's own loop is unchanged: it asks the model, calls step(command, messages), then
appends the assistant message and the tool result. This class replaces the editable
conversation file (LIVE_CTX_MAIN.txt) with VIEW.md (view.py):

  before the command  CLM's newly appended messages become `turn K` lines at the end of
                      the view (sync); if CLM rewrote the messages itself (rollback on
                      overflow), the view is rebuilt from the surviving turns. VIEW.md is
                      uploaded.
  after the command   VIEW.md is read back. If the model changed it, the new view is
                      rendered (view.render) and passed through CLM's own edit gate (fit:
                      an edit may grow the prompt if it still fits the limit); if
                      accepted, the prompt (messages after the protected prefix) becomes
                      exactly the rendered view, and a receipt reports the change.

Everything else is CLM's: the budget readout and nudges (on the rendered prompt), the
output cut, rollback, the finish policy, the counters CLM's run reads (n_ctx_*).
Model edits and automatic `turn K` appends are counted apart (n_view_edits /
n_view_appends). Every step is logged to <run_dir>/view_log.jsonl with the view and the
sha256 of the rendered prompt; edited views are saved to <run_dir>/view_versions/.
"""

from __future__ import annotations

import copy
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable

from clm_harness.context_env import edit_gate
from clm_harness.context_env.env import TRIVIAL_EDIT_FRACTION, TRIVIAL_EDIT_TOKENS, ContextEnv
from clm_harness.context_env.types import StepResult
from clm_harness.utils import tokens as tk

from .view import Line, dump, fingerprint, group_new_messages, parse, render, render_bytes

VIEW_NAME = "VIEW.md"


class ViewContextEnv(ContextEnv):

    def __init__(self, *, obs_text: Callable[[int], str | None], run_dir: str | Path,
                 **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.view_file = f"{self.paths.ctx_dir}/{VIEW_NAME}"
        self.obs_text = obs_text
        self.run_dir = Path(run_dir)
        (self.run_dir / "view_versions").mkdir(parents=True, exist_ok=True)
        self.turns: dict[int, list[dict]] = {}       # turn K -> its stored messages
        self.next_turn = 1
        self.lines: list[Line] = []                  # the current view
        self.line_starts: list[int] = []             # per line: first message index after protect
        self.notes: dict[str, str] = {}              # notes by name (latest text)
        self.ever_dropped: set[str] = set()
        self.n_view_edits = 0
        self.n_view_appends = 0
        self.n_normalized = 0
        self.lines_dropped = 0
        self.lines_restored = 0
        self.n_steps = 0

    # ------------------------------------------------------------------ the view
    def _render(self, lines: list[Line]):
        return render(lines, self.turns, self.obs_text)

    def _new_turn(self, group: list[dict]) -> Line:
        k = self.next_turn
        self.next_turn += 1
        self.turns[k] = [copy.deepcopy(m) for m in group]
        self.n_view_appends += 1
        return Line("turn", k)

    def _rebuild(self, cur: list[dict]) -> list[Line]:
        """View lines for messages CLM rewrote itself (rollback): known turns by
        fingerprint, anything else as new turns, in the messages' order."""
        index: dict[str, tuple[int, int]] = {}
        for k, group in self.turns.items():
            for j, m in enumerate(group):
                index.setdefault(fingerprint(m), (k, j))
        lines: list[Line] = []
        unknown: list[dict] = []
        i = 0
        while i < len(cur):
            hit = index.get(fingerprint(cur[i]))
            if hit and hit[1] == 0:
                k = hit[0]
                g = self.turns[k]
                if [fingerprint(m) for m in cur[i:i + len(g)]] == [fingerprint(m) for m in g]:
                    if unknown:
                        lines += [self._new_turn(x) for x in group_new_messages(unknown)]
                        unknown = []
                    lines.append(Line("turn", k))
                    i += len(g)
                    continue
            unknown.append(cur[i])
            i += 1
        if unknown:
            lines += [self._new_turn(x) for x in group_new_messages(unknown)]
        return lines

    def sync(self, messages: list[dict]) -> None:
        """Bring the view up to date with what CLM appended (or rewrote) since the last step;
        afterwards messages[protect:] == render(view) exactly."""
        cur = messages[self.protect:]
        prev = self._render(self.lines).messages
        if len(cur) >= len(prev) and all(fingerprint(a) == fingerprint(b) for a, b in zip(prev, cur)):
            tail = cur[len(prev):]
            if tail and tail[0].get("role") != "assistant" and self.lines \
                    and self.lines[-1].kind == "turn" and self.lines[-1].id in self.turns:
                lead = []
                while tail and tail[0].get("role") != "assistant":
                    lead.append(tail.pop(0))
                self.turns[self.lines[-1].id] += [copy.deepcopy(m) for m in lead]
            self.lines += [self._new_turn(g) for g in group_new_messages(tail)]
        else:
            self.lines = self._rebuild(cur)
        r = self._render(self.lines)
        if [fingerprint(m) for m in r.messages] != [fingerprint(m) for m in cur]:
            messages[self.protect:] = r.messages          # merge at line boundaries (rare)
            self.n_normalized += 1
        self.line_starts = r.starts

    # ------------------------------------------------------------------ I/O
    async def write_view(self, environment: Any) -> str:
        text = dump(self.lines)
        self.host_mirror.write_text(text)
        await environment.upload_file(str(self.host_mirror), self.view_file)
        return text

    async def read_view(self, environment: Any) -> str | None:
        try:
            await environment.download_file(self.view_file, str(self.host_mirror))
            return self.host_mirror.read_text()
        except Exception:
            return None

    def log(self, rec: dict) -> None:
        with (self.run_dir / "view_log.jsonl").open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def save_state(self) -> None:
        """Turn store and final view, for the offline determinism check."""
        (self.run_dir / "view_turns.json").write_text(json.dumps(
            {str(k): g for k, g in self.turns.items()}, ensure_ascii=False))
        (self.run_dir / "view_final.md").write_text(dump(self.lines))

    # ------------------------------------------------------------------ the step
    async def step(self, command: str, messages: list[dict[str, Any]], *, environment: Any,
                   pending: dict[str, Any] | None = None) -> StepResult:
        self.n_steps += 1
        appends_before = self.n_view_appends
        self.sync(messages)
        written = await self.write_view(environment)
        self.turns_since_edit += 1

        t1 = time.monotonic()
        result = await self.execute(command, environment)
        exec_time = time.monotonic() - t1

        touched = VIEW_NAME in command or self.paths.ctx_dir in command
        read_back = await self.read_view(environment)
        before = tk.count_tokens(messages)[0]
        limit = self.budget.strict_target or self.context_budget_tokens or 0
        note, changed, rec = "", False, {"kind": "sync"}
        if read_back is not None and read_back.strip() != written.strip():
            note, changed, rec = self._apply_view(messages, read_back, before, limit)
        elif touched:
            rc = getattr(result, "return_code", 0)
            note = (f"\n[{VIEW_NAME}: NO change — your command exited {rc}, so the view was not "
                    f"changed (prompt still ~{before} tokens).]" if rc != 0 else
                    f"\n[{VIEW_NAME}: NO change — the view is the same, so the prompt is still "
                    f"~{before} tokens.]")

        stdout_block = self.append_submit_warning(command, self.format_tool_result(result))
        readout = ""
        shown = messages + ([pending] if pending else [])
        if self.context_budget_tokens:
            stdout_block = self.budget.cap_newest_output(shown, stdout_block)
            tokens_now = self.budget.count(shown + [{"role": "tool", "content": stdout_block}])
            shown_budget = self.budget.strict_target or self.context_budget_tokens or 0
            over = "" if tokens_now <= (self.budget.strict_target or 0) else (
                f" — OVER; remove lines from {self.view_file} now")
            readout = f"\n[context: ~{tokens_now}/{shown_budget} tokens{over}]"

        r = self._render(self.lines)
        self.log({"step": self.n_steps, **rec, "appended": self.n_view_appends - appends_before,
                  "lines": [str(x) for x in self.lines], "n_lines": len(self.lines),
                  "sha256": hashlib.sha256(render_bytes(r.messages)).hexdigest(),
                  "prompt_tokens_clm": tk.count_tokens(messages)[0], "touched": touched,
                  "command": command[:200]})
        return StepResult(result=result, ctx_changed=changed, stdout_block=stdout_block,
                          readout=readout, notes=note, exec_time=exec_time, touched_ctx=touched)

    def _apply_view(self, messages: list[dict], text: str, before: int, limit: int):
        parsed = parse(text)
        rec: dict[str, Any] = {"kind": "edit", "bad": parsed.bad}
        if parsed.lines == self.lines:
            note = f"\n[{VIEW_NAME}: NO change — the lines are the same (prompt still ~{before} tokens)]"
            if parsed.bad:
                note = note[:-1] + f"; ignored {len(parsed.bad)} line(s) that are not view lines]"
            return note, False, {**rec, "kind": "same"}
        r = self._render(parsed.lines)
        candidate = messages[:self.protect] + r.messages
        after = tk.count_tokens(candidate)[0]
        rec.update(before=before, after=after, missing=r.missing)
        if after > before and not edit_gate.may_grow(
                allow_growth=self.allow_edit_growth, limit=limit, after=after):
            self.n_ctx_rejected += 1
            rule = edit_gate.reject_rule(allow_growth=self.allow_edit_growth, limit=limit)
            return (f"\n[{VIEW_NAME}: edit REJECTED — it would grow the prompt ~{before}->{after} "
                    f"tokens, so it was NOT applied (still ~{before}). {rule}.]"), False, \
                {**rec, "kind": "rejected"}

        old = [str(x) for x in self.lines]
        new = [str(x) for x in parsed.lines]
        dropped = [x for x in old if x not in set(new)]
        restored = [x for x in new if x in self.ever_dropped and x not in set(old)]
        self.ever_dropped |= set(dropped)
        self.lines_dropped += len(dropped)
        self.lines_restored += len(restored)
        for line in parsed.lines:
            if line.kind == "note":
                self.notes[line.name] = line.text
        missing = set(r.missing)          # lines naming nothing are reported, not kept: a later
        self.lines = [x for x in parsed.lines if str(x) not in missing]   # turn K must not duplicate
        messages[:] = candidate
        self.line_starts = r.starts
        self.n_view_edits += 1
        self.n_ctx_syncs += 1
        editable = max(before - tk.count_tokens(messages[:self.protect])[0], 1)
        if abs(before - after) >= max(TRIVIAL_EDIT_TOKENS, int(TRIVIAL_EDIT_FRACTION * editable)):
            self.n_ctx_syncs_real += 1
        if after > before:
            self.n_ctx_grew += 1
        self.budget.note_compaction()
        self.tokens_at_last_edit = after
        self.turns_since_edit = 0
        (self.run_dir / "view_versions" / f"{self.n_steps:04d}.md").write_text(text)

        extra = f"; dropped {len(dropped)}, restored {len(restored)} line(s)"
        if r.missing:
            extra += f"; {len(r.missing)} line(s) name nothing that exists: " + ", ".join(r.missing[:3])
        if parsed.bad:
            extra += f"; ignored {len(parsed.bad)} line(s) that are not view lines"
        if after > before:
            note = f"\n[{VIEW_NAME}: edit applied — prompt GREW ~{before}->{after} tokens (it fits){extra}]"
        elif limit and after > limit:
            note = (f"\n[{VIEW_NAME}: edit applied — prompt ~{before}->{after} tokens, but STILL OVER the "
                    f"~{limit}-token limit{extra}. Remove more lines now or you'll get one final turn.]")
        else:
            note = f"\n[{VIEW_NAME}: edit applied — prompt ~{before}->{after} tokens, {len(self.lines)} lines{extra}]"
        rec.update(dropped=dropped, restored=restored,
                   notes=[x.name for x in parsed.lines if x.kind == "note"])
        return note, True, rec
