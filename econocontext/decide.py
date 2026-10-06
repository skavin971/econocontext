"""The nine v2 rules: what to do at each Claude Code hook, priced, logged, and returned as the
hook's JSON reply.

Rules (plan numbering):
  1 dont_repeat   identical call, identical output, earlier copy still in context  -> short note
  2 delta_read    Read overlapping an earlier Read of the same unchanged lines     -> only new lines
  3 serve_stored  identical read-only or test command, nothing written since       -> stored output, not re-run
  4 arrival       big result                                                       -> full / slice / preview
  5 worker_report big Agent/Task report                                            -> same as 4
  6 evict         big items Jev judges finished                                    -> compaction keeping the rest
  7 compact       Jev judges a part of the task finished                           -> compaction
  8 placement     a new Agent/Task call while a worker is idle                     -> fresh / brief / resume
  9 invalidate    a write (Edit/Write, or a Bash command that may write)           -> stored results go stale
Replies keep the tool's own response shape (spike 1a: a plain string is ignored), and every
change is explained to the model in `additionalContext` (spike 1a: silent changes alarm it).
"""

import json
import re

from .pricing import lifecycle
from .predictor import chunks
from .session import read_only_bash
from .tokens import count_tokens

WRITES = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
AGENT_TOOLS = {"Agent", "Task"}
TESTS = re.compile(r"^\s*(python3? -m pytest|pytest|npm test|make test|go test|cargo test)\b")
NOTE = "[EconoContext] "


# Tool response shapes (recorded in spike 1a/B) ----------------------------------------
def text_of(tool: str, resp) -> str | None:
    """The text the model sees in a tool's response; None when the shape is unknown."""
    if isinstance(resp, str):
        return resp
    if not isinstance(resp, dict):
        return None
    if tool == "Bash":
        return (resp.get("stdout") or "") + (("\n" + resp["stderr"]) if resp.get("stderr") else "")
    if tool == "Read":
        return (resp.get("file") or {}).get("content") if resp.get("type") == "text" else None
    if tool == "Grep":
        return resp.get("content") or "\n".join(resp.get("filenames") or [])
    if tool == "Glob":
        return "\n".join(resp.get("filenames") or [])
    if tool in AGENT_TOOLS:
        blocks = [b.get("text", "") for b in resp.get("content") or [] if isinstance(b, dict)]
        report = (resp.get("handbackReport") or {}).get("text")
        return "\n".join(blocks + ([report] if report else []))
    return None


def with_text(tool: str, resp, text: str, start_line: int | None = None):
    """The same response with its text replaced; None when the shape is unknown."""
    if isinstance(resp, str):
        return text
    if not isinstance(resp, dict):
        return None
    lines = text.count("\n") + 1
    if tool == "Bash":
        return {**resp, "stdout": text, "stderr": ""}
    if tool == "Read" and resp.get("type") in ("text", "file_unchanged"):
        file = {**(resp.get("file") or {}), "content": text, "numLines": lines}
        if start_line is not None:
            file["startLine"] = start_line
        return {**resp, "type": "text", "file": file}
    if tool == "Grep" and resp.get("mode") == "content":
        return {**resp, "content": text, "numLines": lines}
    if tool == "Glob":
        names = text.splitlines()
        return {**resp, "filenames": names, "numFiles": len(names)}
    if tool in AGENT_TOOLS:
        out = {**resp, "content": [{"type": "text", "text": text}]}
        if isinstance(resp.get("handbackReport"), dict):
            out["handbackReport"] = {**resp["handbackReport"], "text": text}
        return out
    return None


def reply_post(output=None, note: str | None = None) -> dict:
    spec = {"hookEventName": "PostToolUse"}
    if output is not None:
        spec["updatedToolOutput"] = output
    if note:
        spec["additionalContext"] = NOTE + note
    return {"hookSpecificOutput": spec}


def reply_pre(updated_input: dict | None = None, deny: str | None = None) -> dict:
    spec = {"hookEventName": "PreToolUse"}
    if deny:
        spec.update(permissionDecision="deny", permissionDecisionReason=NOTE + deny)
    elif updated_input is not None:
        spec.update(permissionDecision="allow", updatedInput=updated_input)
    return {"hookSpecificOutput": spec}


# Context for a decision --------------------------------------------------------------
class Ctx:
    """What a rule may use: the session, config, prices, predictor, forced rules, and the
    session's progress (calls so far, current prompt size) from the transcript."""

    def __init__(self, session, cfg: dict, prices, predictor, force: set[str], calls: int,
                 prompt: int, task: str, convo):
        self.s, self.cfg, self.p, self.predictor, self.force = session, cfg, prices, predictor, force
        self.calls, self.prompt, self.task, self._convo = calls, prompt, task, convo

    @property
    def calls_left(self) -> int:
        return max(3, self.cfg["session"]["calls_total"] - self.calls)

    def convo(self):
        return self._convo() if callable(self._convo) else self._convo

    def predict(self, method: str, *args):
        """Ask the predictor; on failure fall back to the prior and say why."""
        try:
            answers, usage = getattr(self.predictor, method)(self.task, self.convo(), *args)
            return answers, usage, self.predictor.name
        except Exception as exc:  # fail open: the agent never stops because of the predictor
            from .predictor.prior import Prior
            answers, _ = getattr(Prior(), method)(self.task, None, *args)
            return answers, None, f"prior ({str(exc)[:120]})"


# PreToolUse: rules 3 and 8 -------------------------------------------------------------
def pre_tool(ctx: Ctx, ev: dict) -> dict:
    tool, args, agent = ev.get("tool_name"), ev.get("tool_input") or {}, ev.get("agent_id") or "main"
    if tool == "Bash":
        command = args.get("command", "")
        prev = ctx.s.last_same(agent, tool, args)
        if (prev and prev["epoch"] == ctx.s.epoch and prev["output"] is not None
                and (read_only_bash(command) or TESTS.match(command))):
            served = {**args, "command": f"cat <<'ECONO_EOF'\n{prev['output']}\nECONO_EOF"}
            if ev.get("tool_use_id"):  # PostToolUse will see the rewritten command; remember the original
                ctx.s.set(f"served:{ev['tool_use_id']}", args)
            ctx.s.log("3 serve_stored", "serve", prev["id"], note=command[:200])
            return reply_pre(updated_input=served)
    if tool in AGENT_TOOLS:
        return placement(ctx, args)
    return {}


def placement(ctx: Ctx, args: dict) -> dict:
    idle = ctx.s.get("idle_workers", {})
    if not idle:
        return {}
    worker_id, worker_type = next(iter(idle.items()))
    held = [dict(r) for r in ctx.s.in_context(worker_id)]
    if not held:
        return {}
    items = [{"id": r["id"], "tool": r["tool"], "input": json.loads(r["input"]), "tokens": r["tokens"]}
             for r in held]
    answers, usage, source = ctx.predict("subtask", args.get("prompt", ""), items)
    needed = answers["needed"]
    history = sum(r["shown_tokens"] for r in held)
    prices = lifecycle.placement(ctx.p, history, ctx.cfg["session"]["calls_total"] / 3,
                                 ctx.cfg["session"]["fixed_prefix"],
                                 [(needed.get(r["id"], 0.0), r["tokens"]) for r in held], ctx.prompt)
    forced = "8 placement" in ctx.force
    if forced or prices["resume"] < prices["fresh"]:
        ctx.s.log("8 placement", "resume", forced=forced, prices=prices, answers={"source": source, **answers},
                  jev_usage=usage, note=worker_id)
        return reply_pre(deny=(f"An idle worker ({worker_type or 'agent'} {worker_id}) already holds the "
                               f"files this sub-task needs. Continue that worker instead of starting a new "
                               f"one: send it this sub-task with SendMessage (to: '{worker_id}')."))
    brief = [r for r in held if needed.get(r["id"], 0.0) >= 0.5]
    if brief:
        text = "\n\n".join(f"[from an earlier worker: {r['tool']} {r['input']}]\n{r['output'][:4000]}"
                           for r in brief[:5])
        ctx.s.log("8 placement", "brief", prices=prices, answers={"source": source, **answers},
                  jev_usage=usage, note=worker_id)
        return reply_pre(updated_input={**args, "prompt": args.get("prompt", "") +
                                        "\n\nContext an earlier worker already gathered:\n" + text})
    ctx.s.log("8 placement", "fresh", prices=prices, answers={"source": source, **answers}, jev_usage=usage)
    return {}


# PostToolUse: rules 9, 1, 2, 3 (retrieval), 4, 5 ---------------------------------------
def post_tool(ctx: Ctx, ev: dict) -> dict:
    tool, args, agent = ev.get("tool_name"), ev.get("tool_input") or {}, ev.get("agent_id") or "main"
    resp = ev.get("tool_response")
    original = ctx.s.get(f"served:{ev.get('tool_use_id')}") if ev.get("tool_use_id") else None
    if original is not None:  # rule 3 served this call from the session database
        text = text_of(tool, resp) or ""
        ctx.s.add_item(agent, tool, original, text, count_tokens(text))
        ctx.s.set(f"served:{ev['tool_use_id']}", None)
        return reply_post(None, "Nothing was written since you last ran this exact command, so this is "
                                "its saved output (not re-run).")
    if tool in WRITES or (tool == "Bash" and not read_only_bash(args.get("command", ""))
                          and not TESTS.match(args.get("command", ""))):
        epoch = ctx.s.bump("epoch")
        ctx.s.log("9 invalidate", "bump", note=f"{tool} -> epoch {epoch}")
    if tool == "SubagentHandback":
        return {}

    # A repeated Read that Claude Code answered with its "unchanged" stub, for content we had
    # shrunk or that left the context: give the full stored copy back.
    if tool == "Read" and isinstance(resp, dict) and resp.get("type") == "file_unchanged":
        reads = ctx.s.db.execute("SELECT * FROM items WHERE agent=? AND tool='Read' AND "
                                 "json_extract(input, '$.file_path')=? ORDER BY id DESC LIMIT 1",
                                 (agent, args.get("file_path"))).fetchall()
        prev = reads[0] if reads else None
        if prev and (prev["form"] != "full" or not prev["in_context"]):
            ctx.s.update_item(prev["id"], form="full", shown_tokens=prev["tokens"], in_context=1)
            ctx.s.log("3 serve_stored", "restore", prev["id"])
            return reply_post(with_text(tool, resp, prev["output"], start_line=args.get("offset") or 1),
                              "Here is the full content you asked for again.")
        return {}

    text = text_of(tool, resp)
    if text is None:
        return {}
    tokens = count_tokens(text)
    prev = ctx.s.full_copy_in_context(agent, tool, args)
    item_id = ctx.s.add_item(agent, tool, args, text, tokens)

    # Rule 1: identical output of an identical call whose earlier full copy is still in context.
    # (When the earlier copy was shrunk or compacted away, this new full copy is the re-read.)
    if prev and prev["output"] == text and tokens > 50:
        note_text = f"Same output as your earlier identical call (item {prev['id']}); unchanged and still above."
        prices = lifecycle.note(ctx.p, count_tokens(note_text), tokens, ctx.calls_left)
        ctx.s.update_item(item_id, form="note", shown_tokens=count_tokens(note_text))
        ctx.s.log("1 dont_repeat", "note", item_id, prices=prices)
        return reply_post(with_text(tool, resp, note_text), None)

    # Rule 2: a Read overlapping earlier Reads of the same lines (identical content).
    if tool == "Read":
        trimmed = delta_read(ctx, agent, args, resp, item_id)
        if trimmed:
            return trimmed

    # Rules 4 and 5: big results are priced.
    if tokens >= ctx.cfg["session"]["big_tokens"]:
        return arrival(ctx, tool, args, resp, text, tokens, item_id)
    return {}


def delta_read(ctx: Ctx, agent: str, args: dict, resp, item_id: int) -> dict:
    file = resp.get("file") or {}
    start, lines = file.get("startLine") or 1, (file.get("content") or "").split("\n")
    end = start + len(lines) - 1
    seen: dict[int, str] = {}
    for prev in ctx.s.reads_of(agent, args.get("file_path")):
        if prev["id"] == item_id or prev["form"] not in ("full",):
            continue
        p_args = json.loads(prev["input"])
        p_start = p_args.get("offset") or 1
        for i, line in enumerate((prev["output"] or "").split("\n")):
            seen.setdefault(p_start + i, line)
    shown = [n for n in range(start, end + 1) if seen.get(n) == lines[n - start]]
    if not shown or len(shown) < 5:
        return {}
    # Keep one contiguous remainder (overlap at the start or the end); otherwise leave it.
    if shown[0] == start and shown == list(range(start, shown[-1] + 1)):
        keep_from, keep_to = shown[-1] + 1, end
    elif shown[-1] == end and shown == list(range(shown[0], end + 1)):
        keep_from, keep_to = start, shown[0] - 1
    else:
        return {}
    remainder = "\n".join(lines[keep_from - start: keep_to - start + 1])
    tokens_full = count_tokens("\n".join(lines))
    kept = count_tokens(remainder)
    ctx.s.update_item(item_id, form="slice", shown_tokens=kept)
    ctx.s.log("2 delta_read", "trim", item_id, prices=lifecycle.note(ctx.p, kept, tokens_full, ctx.calls_left))
    if keep_from > keep_to:
        return reply_post(with_text("Read", resp, "", start_line=start),
                          f"Lines {start}-{end} are unchanged and already above from your earlier read.")
    return reply_post(with_text("Read", resp, remainder, start_line=keep_from),
                      f"Lines {shown[0]}-{shown[-1]} are unchanged and already above from your earlier "
                      f"read; showing only lines {keep_from}-{keep_to}.")


def arrival(ctx: Ctx, tool: str, args: dict, resp, text: str, tokens: int, item_id: int) -> dict:
    rule = "5 worker_report" if tool in AGENT_TOOLS else "4 arrival"
    lines = text.split("\n")
    spans = chunks(text, ctx.cfg["session"]["chunk_lines"])
    item = {"tool": tool, "input": args, "text": text, "chunks": spans}
    answers, usage, source = ctx.predict("arrival", item)
    p_need = answers["needed_again"]
    half = ctx.cfg["session"]["preview_lines"] // 2
    preview_text = "\n".join(lines[:half] + [f"... [{len(lines) - 2 * half} lines not shown] ..."] + lines[-half:])
    shown, miss, texts = {"preview": count_tokens(preview_text)}, {"preview": p_need}, {"preview": preview_text}
    relevant = answers.get("relevant")
    if relevant:
        order = sorted(relevant, key=relevant.get, reverse=True)
        picked, mass = [], 0.0
        for i in order:
            if relevant[i] < ctx.cfg["session"]["slice_min_prob"] and picked:
                break
            picked.append(i)
            mass += relevant[i]
            if mass >= 0.8:
                break
        picked_spans = sorted(spans[i] for i in picked if i < len(spans))
        slice_text = "\n".join(f"[lines {a}-{b}]\n" + "\n".join(lines[a - 1:b]) for a, b in picked_spans)
        shown["slice"], miss["slice"], texts["slice"] = count_tokens(slice_text), p_need * (1 - mass), slice_text
    prices = lifecycle.arrival(ctx.p, tokens, shown, miss, ctx.calls_left, ctx.prompt,
                               lifecycle.reread_at(answers.get("lifetime"), ctx.calls_left))
    forced = rule in ctx.force
    choice = min(prices, key=prices.get)
    if forced and choice == "full":
        choice = "slice" if "slice" in shown else "preview"
    ctx.s.log(rule, choice, item_id, forced=forced, prices=prices,
              answers={"source": source, **answers}, jev_usage=usage)
    if choice == "full":
        return {}
    output = with_text(tool, resp, texts[choice])
    if output is None:
        return {}
    ctx.s.update_item(item_id, form=choice, shown_tokens=shown[choice])
    what = "the parts most relevant to your task" if choice == "slice" else "the first and last lines"
    return reply_post(output, f"This output has {len(lines)} lines (about {tokens} tokens); you are seeing "
                              f"{what}. The full output is saved: repeat the identical call to get all of it.")


# Between calls: rules 6 and 7 ----------------------------------------------------------
def live_check(ctx: Ctx) -> dict | None:
    """Maybe flag a compaction. Returns the flag (also stored in state) or None."""
    cfg = ctx.cfg["session"]
    if ctx.calls - ctx.s.get("last_live_check", -999) < cfg["live_check_every"]:
        return None
    if ctx.s.get("compact"):
        return None
    items = [dict(r) for r in ctx.s.in_context("main")]
    conversation = ctx.prompt - cfg["fixed_prefix"]
    # Forced (spike 1b): once, and only after the session has done some work.
    forced = bool(ctx.force & {"6 evict", "7 compact"}) and ctx.s.get("compactions", 0) == 0 \
        and ctx.calls >= cfg["force_compact_after"]
    if conversation < cfg["compact_min_conversation"] and not forced:
        return None
    ctx.s.set("last_live_check", ctx.calls)
    big = [{"id": r["id"], "tool": r["tool"], "input": json.loads(r["input"]), "tokens": r["tokens"]}
           for r in items if r["shown_tokens"] >= cfg["big_tokens"]]
    answers, usage, source = ctx.predict("live", big)
    done = answers["done"]
    keep_ids = {r["id"] for r in items if done.get(r["id"], 0.0) < 0.5}
    dropped = [(1 - done.get(r["id"], 0.0), r["tokens"]) for r in items if r["id"] not in keep_ids]
    prices = lifecycle.compaction(ctx.p, ctx.prompt, cfg["fixed_prefix"], cfg["summary_tokens"],
                                  ctx.calls_left, dropped)
    finished_big = [i["id"] for i in big if done.get(i["id"], 0.0) >= 0.5]
    rule = "7 compact" if answers.get("portion_done", 0.0) >= 0.6 else "6 evict"
    worth = prices["compact"] < prices["keep"]
    go = forced or (worth and (rule == "7 compact" or finished_big))
    ctx.s.log(rule, "compact" if go else "wait", forced=forced, prices=prices,
              answers={"source": source, **answers}, jev_usage=usage)
    if not go:
        return None
    keep = [r for r in items if r["id"] in keep_ids]
    keep_desc = "; ".join(f"the output of {r['tool']} {json.dumps(json.loads(r['input']))[:120]}" for r in keep[:8])
    instructions = ("Summarize the finished work briefly: what was found, what was changed, what remains. "
                    + (f"Keep these verbatim because they are still needed: {keep_desc}. " if keep else "")
                    + "Drop other tool outputs; they are saved and can be fetched by repeating the call.")
    flag = {"instructions": instructions, "keep_ids": sorted(keep_ids), "rule": rule}
    ctx.s.set("compact", flag)
    return flag


def post_compact(session, ev: dict) -> None:
    flag = session.get("compact") or {}
    dropped = session.drop_from_context("main", set(flag.get("keep_ids", [])))
    session.log(flag.get("rule", "7 compact"), "compacted",
                note=json.dumps({"trigger": ev.get("trigger"), "dropped_items": dropped,
                                 "summary_tokens": count_tokens(ev.get("compact_summary") or "")}))
    session.set("compact", None)
    session.bump("compactions")
