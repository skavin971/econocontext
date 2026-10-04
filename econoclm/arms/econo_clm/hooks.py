"""EconoCLM's per-command hook: what happens around CLM's own ContextEnv.step.

CLM runs the command and applies any context edit; then, for EconoCLM only:

  1. Archive  the full output (stdout + stderr) is written to <run_dir>/obs/<id>.txt,
              uploaded to /tmp/econo/obs/<id>.txt in the sandbox, indexed, recorded.
  2. Tag      "[obs <id>]" goes in front of the output the model sees, plus
              "(cut in context; full: econo get <id>)" when CLM cut it. With cut_lines
              (arms v1.1/v1.2) a cut that CLM made by keeping the head and tail is shown
              as the exact missing lines: "[obs 3] lines 120-310 not shown: econo get 3
              120-310" (missing_lines); any other cut keeps the v1 wording.
  3. Stale    file reads in the command are detected (bash_reads); one sha1sum in the
              sandbox checks every file read so far. A file is stale if it changed
              since the read AND that read's [obs N] tag is still in the context.
  4. Quote    if CLM applied a context edit: the [econo] edit line (edit_quote.py).
  5. Status   the [econo] status line after every command (status_line.py).

Fail open: every change is made on a COPY of CLM's StepResult. If anything raises,
the error is recorded in hook_errors and CLM's original StepResult is returned.

Usage tracking (on_response) runs after each model call: it keeps the last call's
cached/uncached tokens (from litellm's usage, else from the gateway's ledger row), the
reply's thinking tokens keyed by its thought signature, and a HiddenMeter
(quote/hidden.py) that measures how much hidden thinking Gemini read in the call and
keeps k (Gemini tokens per CLM-tokenizer token of visible text) calibrated.
"""

import asyncio
import dataclasses
import hashlib
import json
import logging
import random
import shlex
import time
import traceback
from pathlib import Path
from typing import Any, Callable

from ...core import bash_reads, prices
from ...core.gateway_ledger import Ledger
from ...core.meter import price_call_usd
from ...core.run_store import RunStore
from ...core.usage import Counts, counts, to_usage
from ...quote.edit_quote import edit_quote
from ...quote.hidden import HiddenMeter, sig_key, signature
from ...quote.messages import default_count, obs_ids
from ...quote.status_line import status_line

log = logging.getLogger("econoclm.hooks")

SANDBOX_DIR = "/tmp/econo"
SETUP_COMMAND = (f"mkdir -p {SANDBOX_DIR}/obs && : > {SANDBOX_DIR}/index.tsv "
                 f"&& : > {SANDBOX_DIR}/log.tsv")
LEDGER_WAIT_S = 0.5
HIT_WINDOW = 10        # recent calls for the cache-hit rate shown in the quote and status line
RETRY_REASONS = ("malformed_function_call",)  # resent below CLM's count (results_table.py)


def missing_lines(stdout: str, stderr: str, max_chars: int,
                  obs_cfg: dict | None = None) -> tuple[int, int] | None:
    """The lines of the saved output (obs N) that CLM's head/tail cut leaves out, as
    1-based (first, last) line numbers, boundary lines included; None if not cut.

    CLM shows stdout + "\n" + stderr (just stderr if stdout is empty), rstripped; if
    that is longer than max_chars it keeps head_chars and tail_chars (default 5000
    each, halved to fit max_chars) and elides the middle (ContextEnv.truncate_observation).
    The saved output is stdout + "\n" + stderr, so with an empty stdout it starts with
    one extra empty line."""
    output = stdout or ""
    if stderr:
        output += f"\n{stderr}" if output else stderr
    output = output.rstrip()
    if len(output) <= max_chars:
        return None
    cfg = obs_cfg or {}
    head_n, tail_n = int(cfg.get("head_chars", 5000)), int(cfg.get("tail_chars", 5000))
    if head_n + tail_n > max_chars:
        head_n = tail_n = max_chars // 2
    end = len(output) - tail_n                       # first char of the shown tail
    if end <= head_n:
        return None
    first = output.count("\n", 0, head_n) + 1
    last = output.count("\n", 0, end - 1) + 1
    offset = 1 if (not stdout and stderr) else 0
    return first + offset, last + offset


def usage_dict(response: Any) -> dict | None:
    """litellm's usage object as a plain dict (None if absent)."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    if hasattr(usage, "model_dump"):
        return usage.model_dump()
    return dict(usage) if isinstance(usage, dict) else None


def reply_signature(response: Any) -> str | None:
    """The thought signature on the reply's tool calls (None if absent)."""
    try:
        msg = response.choices[0].message
        msg = msg.model_dump() if hasattr(msg, "model_dump") else dict(msg)
    except Exception:
        return None
    return signature(msg)


class EconoHooks:
    def __init__(self, run_dir: str | Path, run_id: str, *, observation_max_chars: int,
                 protect: Callable[[], int], state_dir: str,
                 gateway_db: str | Path | None = None, econo_path: str | None = None,
                 count: Callable[[list[dict]], int] = default_count,
                 cut_lines: bool = False, obs_cfg: dict | None = None):
        self.run_dir = Path(run_dir)
        self.obs_dir = self.run_dir / "obs"
        self.obs_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self.store = RunStore(self.run_dir / "econo.sqlite")
        self.observation_max_chars = observation_max_chars
        self.protect = protect           # CLM's protected prefix length (grows on rollback)
        self.state_dir = state_dir       # CLM's persistent-shell state (holds the cwd)
        self.gateway_db = Path(gateway_db) if gateway_db else None
        self.count = count
        self.econo_path = econo_path     # the econo tool inside the sandbox (for verify_gets)
        self.cut_lines = cut_lines       # v1.1/v1.2: tags name the exact missing lines
        self.obs_cfg = obs_cfg or {}     # CLM's head_chars / tail_chars, for missing_lines

        self.turn = 0                    # commands run so far
        self.n_obs = 0
        self.n_calls = 0                 # model calls seen by on_response
        self.n_hook_errors = 0
        self.last: Counts | None = None  # the last model call's tokens
        self.k = 1.0                     # provider tokens per CLM-tokenizer token (visible text)
        self.thinking: dict[str, int] = {}  # thought-signature key -> that call's thinking tokens
        self.meter = HiddenMeter()
        self.hit_log: list[bool] = []    # per model call after the first: any cached tokens?
        self.last_read: tuple[int, int, int] | None = None  # (Gemini read, CLM count, hidden)
        self.agent_cost_usd = 0.0        # fallback run cost (from litellm usage)
        self.reads: dict[str, tuple[str, int, int]] = {}  # path -> (sha1, turn, obs_id), latest read

    # ------------------------------------------------------------------ errors
    def error(self, where: str, exc: BaseException) -> None:
        self.n_hook_errors += 1
        text = "".join(traceback.format_exception_only(type(exc), exc)).strip()
        log.warning("econo hook error in %s: %s", where, text)
        try:
            self.store.insert("hook_errors", turn=self.turn, where=where,
                              error=text + "\n" + traceback.format_exc()[-2000:])
        except Exception:
            log.exception("could not record a hook error")

    # ------------------------------------------------------------------ setup
    async def setup(self, environment: Any) -> None:
        await environment.exec(command=SETUP_COMMAND, timeout_sec=30)

    # ------------------------------------------------------------------ model calls
    async def on_response(self, response: Any, messages: list[dict]) -> None:
        self.n_calls += 1
        usage = usage_dict(response)
        c = counts(usage)
        details = (usage or {}).get("prompt_tokens_details")
        if not (isinstance(details, dict) and details.get("cached_tokens") is not None):
            row = await self.ledger_row(self.n_calls - 1)
            if row is not None:
                c = Counts(prompt=row["prompt_tokens"], cached=row["cached_tokens"],
                           uncached=row["uncached_tokens"], output=row["output_tokens"],
                           reasoning=row["reasoning_tokens"])
        self.last = c
        if self.n_calls > 1:
            self.hit_log.append(bool(c.cached))
        ours = self.count(messages)
        hidden = self.meter.observe(messages, c.prompt, ours, self.thinking)
        self.k = self.meter.k
        self.last_read = (c.prompt, ours, hidden) if hidden is not None else None
        sig = reply_signature(response)   # this reply's thinking: read by later calls
        if sig:
            self.thinking[sig_key(sig)] = c.reasoning or 0
        self.agent_cost_usd += price_call_usd(to_usage(usage), prices.RATES) if usage else 0.0

    def hits(self) -> tuple[int, int] | None:
        """(calls with a cache hit, calls) over the last HIT_WINDOW calls (the run's first
        call is left out: nothing can be cached yet)."""
        recent = self.hit_log[-HIT_WINDOW:]
        return (sum(recent), len(recent)) if recent else None

    async def ledger_row(self, n: int) -> dict | None:
        """The gateway's row for this run's n-th successful call (0-based; failed
        attempts are rows too, so call_no alone could be off). The gateway writes it
        just after replying, so wait up to LEDGER_WAIT_S for it."""
        if not self.gateway_db or not self.gateway_db.exists():
            return None
        deadline = time.monotonic() + LEDGER_WAIT_S
        ledger = Ledger(self.gateway_db)
        try:
            while True:
                rows = ledger.rows("SELECT * FROM calls WHERE run_id=? AND http_status=200 "
                                   "AND COALESCE(finish_reason, '') NOT IN (?) "
                                   "ORDER BY call_no LIMIT 1 OFFSET ?",
                                   (self.run_id, *RETRY_REASONS, n))
                if rows or time.monotonic() >= deadline:
                    return rows[0] if rows else None
                await asyncio.sleep(0.05)
        finally:
            ledger.close()

    def run_cost(self) -> float:
        """This run's spend so far: the gateway's rows, else our own usage sum."""
        if self.gateway_db and self.gateway_db.exists():
            ledger = Ledger(self.gateway_db)
            try:
                return ledger.run_spend(self.run_id)
            finally:
                ledger.close()
        return self.agent_cost_usd

    # ------------------------------------------------------------------ the step wrapper
    async def step(self, orig, command: str, messages: list[dict], *, environment: Any,
                   pending: dict | None = None):
        before = [dict(m) for m in messages]  # shallow is enough: CLM replaces messages on edits
        sr = await orig(command, messages, environment=environment, pending=pending)
        self.turn += 1
        try:
            return await self.augment(sr, command, before, messages, environment, pending)
        except Exception as exc:
            self.error("step", exc)
            return sr

    async def augment(self, sr, command, before, messages, environment, pending):
        out = dataclasses.replace(sr)  # never touch CLM's own result
        turn = self.turn

        # 1. Archive the full output.
        r = sr.result
        full = (r.stdout or "") + ("\n" + r.stderr if r.stderr else "")
        data = full.encode("utf-8", errors="surrogateescape")
        self.n_obs += 1
        obs_id = self.n_obs
        host_path = self.obs_dir / f"{obs_id}.txt"
        host_path.write_bytes(data)
        await environment.upload_file(str(host_path), f"{SANDBOX_DIR}/obs/{obs_id}.txt")
        # CLM shows stdout + "\n" + stderr, rstripped, cut to observation_max_chars, and
        # may cut it again to fit the budget: if that text is not all shown, it was cut.
        shown = (r.stdout or "")
        if r.stderr:
            shown += f"\n{r.stderr}" if shown else r.stderr
        shown = shown.rstrip()
        cut = len(full) > self.observation_max_chars or (bool(shown) and shown not in sr.stdout_block)
        line = "\t".join([str(obs_id), str(turn), str(len(data)),
                          " ".join(command[:100].split())])
        res = await environment.exec(
            command=(f"printf '%s\\n' {shlex.quote(line)} >> {SANDBOX_DIR}/index.tsv; "
                     f"cat {shlex.quote(self.state_dir)}/cwd 2>/dev/null"),
            timeout_sec=30)
        cwd = ((getattr(res, "stdout", None) or "").strip().splitlines() or ["/"])[-1]
        self.store.insert("observations", obs_id=obs_id, turn=turn, command=command,
                          host_path=str(host_path), bytes=len(data),
                          sha1=hashlib.sha1(data).hexdigest(), cut_in_context=int(cut))

        # 2. Tag it.
        tag = self.cut_tag(obs_id, r, sr, cut)
        out.stdout_block = tag + "\n" + sr.stdout_block

        # 3. Stale files.
        stale = await self.stale_check(command, full, cwd, obs_id, turn, messages, environment)

        # 4. Quote, if CLM applied an edit.
        c = self.last.cached if self.last else None
        if sr.ctx_changed:
            q = edit_quote(before, messages, c, protect=self.protect(), k=self.k,
                           count=self.count, thinking=self.thinking, mode=self.meter.mode,
                           hits=self.hits())
            if q is not None:
                self.store.insert(
                    "edits", turn=turn, before_tokens=q.before_tokens,
                    after_tokens=q.after_tokens, first_change_msg=q.first_change_msg,
                    prefix_tokens_p=q.prefix_tokens_p, cached_c=q.cached_c,
                    predicted_reprocess_R=q.R, predicted_cost_usd=q.extra_usd,
                    saving_per_call_usd=q.saving_usd, payoff_calls=q.payoff_calls,
                    removed_obs=json.dumps(q.removed), calibration=q.calibration, text=q.line,
                    next_call=self.n_calls)
                out.notes = sr.notes + "\n" + q.line

        # 5. Status line.
        st = status_line(messages + ([pending] if pending else []), cached_c=c,
                         uncached=self.last.uncached if self.last else None,
                         run_cost_usd=self.run_cost(), n_stored=self.n_obs, stale=stale,
                         protect=self.protect(), k=self.k, count=self.count,
                         thinking=self.thinking, mode=self.meter.mode, read=self.last_read,
                         hits=self.hits())
        self.store.insert("status_lines", turn=turn, text=st.line, stale_paths=json.dumps(stale))
        out.readout = sr.readout + "\n" + st.line
        return out

    def cut_tag(self, obs_id: int, r: Any, sr: Any, cut: bool) -> str:
        if not cut:
            return f"[obs {obs_id}]"
        rng = missing_lines(r.stdout or "", r.stderr or "", self.observation_max_chars,
                            self.obs_cfg) if self.cut_lines else None
        if rng and not self.head_and_tail_shown(r, sr.stdout_block):
            rng = None                   # cut again to fit the budget: range unknown
        if rng:
            a, b = rng
            return f"[obs {obs_id}] lines {a}-{b} not shown: econo get {obs_id} {a}-{b}"
        return f"[obs {obs_id}] (cut in context; full: econo get {obs_id})"

    def head_and_tail_shown(self, r: Any, block: str) -> bool:
        """Did the model see CLM's whole head/tail cut (no second cut for the budget)?"""
        output = r.stdout or ""
        if r.stderr:
            output += f"\n{r.stderr}" if output else r.stderr
        output = output.rstrip()
        head_n = int(self.obs_cfg.get("head_chars", 5000))
        tail_n = int(self.obs_cfg.get("tail_chars", 5000))
        if head_n + tail_n > self.observation_max_chars:
            head_n = tail_n = self.observation_max_chars // 2
        return output[:head_n] in block and output[-tail_n:] in block

    async def stale_check(self, command, full, cwd, obs_id, turn, messages, environment):
        """Record this command's file reads; return the paths that are stale now."""
        try:
            _, line_reads = bash_reads.shell_reads(command, full, cwd)
        except Exception as exc:  # an unparsable command reads nothing we know of
            self.error("bash_reads", exc)
            line_reads = []
        new_paths = sorted({lr.path for lr in line_reads})
        tracked = sorted(set(self.reads) | set(new_paths))
        if not tracked:
            return []
        res = await environment.exec(
            command="sha1sum -- " + " ".join(shlex.quote(p) for p in tracked) + " 2>/dev/null",
            timeout_sec=30)
        now: dict[str, str] = {}
        for ln in (getattr(res, "stdout", None) or "").splitlines():
            parts = ln.split(None, 1)
            if len(parts) == 2 and not parts[0].startswith("\\"):
                now[parts[1].lstrip("*")] = parts[0]
        for p in new_paths:
            if p in now:
                self.reads[p] = (now[p], turn, obs_id)
                self.store.insert("files", path=p, sha1=now[p], turn_read=turn, obs_id=obs_id)
        in_context = obs_ids(messages)
        return [p for p, (h, _, oid) in sorted(self.reads.items())
                if p in now and now[p] != h and oid in in_context]

    # ------------------------------------------------------------------ end of run
    async def verify_gets(self, environment: Any, n: int = 3) -> None:
        """Gate 5 check, while the sandbox still exists: `econo get N | sha1sum` in the
        sandbox vs the host copy's sha1, for up to n random saved outputs. Runs AFTER
        log.tsv was downloaded, so these calls never count as the model's econo use."""
        if not self.n_obs or not self.econo_path:
            return
        ids = sorted(random.Random(self.run_id).sample(range(1, self.n_obs + 1),
                                                       min(n, self.n_obs)))
        script = "; ".join(f"echo {i} $(sh {shlex.quote(self.econo_path)} get {i} | sha1sum)"
                           for i in ids)
        res = await environment.exec(command=script, timeout_sec=60)
        got = {}
        for ln in (getattr(res, "stdout", None) or "").splitlines():
            parts = ln.split()
            if len(parts) >= 2 and parts[0].isdigit():
                got[int(parts[0])] = parts[1]
        host = {r["obs_id"]: r["sha1"] for r in self.store.rows("SELECT obs_id, sha1 FROM observations")}
        for i in ids:
            self.store.insert("get_checks", obs_id=i, host_sha1=host.get(i),
                              container_sha1=got.get(i), match=int(got.get(i) == host.get(i)))

    async def finish(self, environment: Any) -> None:
        """Bring the sandbox's econo log (and the model's notes.db) home; load the log."""
        for name in ("log.tsv", "notes.db"):
            try:
                await environment.download_file(f"{SANDBOX_DIR}/{name}", str(self.run_dir / name))
            except Exception as exc:
                if name == "log.tsv":
                    self.error("finish", exc)
        path = self.run_dir / "log.tsv"
        if path.exists():
            for ln in path.read_text(errors="replace").splitlines():
                parts = ln.split("\t", 2)
                if len(parts) >= 2:
                    try:
                        ts = float(parts[0])
                    except ValueError:
                        ts = None
                    self.store.insert("econo_ops", ts=ts, op=parts[1],
                                      args=parts[2] if len(parts) > 2 else "")
        try:
            await self.verify_gets(environment)
        except Exception as exc:
            self.error("verify_gets", exc)
        (self.run_dir / "summary.json").write_text(json.dumps({
            "run_id": self.run_id, "commands": self.turn, "saved_outputs": self.n_obs,
            "model_calls_seen": self.n_calls, "hook_errors": self.n_hook_errors,
        }, indent=2) + "\n")
