"""Exact positions for every rewrite, and the re-read split into edit-caused vs post-edit misses.

  python -m econoclm.analysis.rewrite_positions runs/<phase> [--arms raw,econo]

Needs the gateway's logged bodies (--log-bodies) and visible_tokens.json
(count_tokens.py). For each rewrite (rereads.py) at call i, in Gemini tokens:

  P   first changed position: countTokens(request_i[:j] + the text message j still
      shares with its old version) + OVERHEAD + the hidden thinking before message j in
      call i's billing state (exact: none/live/all, nearest to the exact hidden part)
  A   start of the newly appended turns (request_i[:a] likewise)
  p   the live estimate the quote would have used (HiddenMeter state after call i-1)
  R   the quote's bound, max(0, c_(i-1) - p); R_likely = max(0, min(c_(i-1), A_live) - p)
      (deleted text is not re-read); R_exact = max(0, min(c_(i-1), A) - P) with P in call
      i-1's state

  P'  = P, or 0 when P < CACHE_MIN_TOKENS (Gemini's cache then serves nothing)
  edit-caused re-read = max(0, min(A, c_(i-1)) - max(c_i, P'))
                        cached tokens the edit put out of reach and that are not cached now;
                        "lost prefix" = the part of it before P (only when P < the minimum)
  post-edit misses    = extra_uncached - edit-caused   (misses the edit cannot explain:
                        reachable prefix not served from cache, or never-cached text)
  bound violation     = edit-caused > R

countTokens is free; it is called twice per rewrite (prefixes j and a). Results go to
rewrite_positions.json and rewrite_positions.md next to the ledger.
"""

import argparse
import json
import statistics
from pathlib import Path

from ..core.gateway import load_secrets
from ..quote.edit_quote import CACHE_MIN_TOKENS, edit_quote, reachable_prefix
from ..quote.hidden import MODES, HiddenMeter, hidden_per_message, sig_key, signature
from ..quote.messages import default_count, first_change, shared_text
from .common import RETRY_REASONS, load_runs
from .count_tokens import count, endpoint, to_native
from .rereads import appended_start, call_rereads

OVERHEAD = 11   # billed prompt_tokens - countTokens on every call with no hidden part


def true_mode(msgs: list[dict], thinking: dict[str, int], hidden_exact: int) -> str:
    totals = {m: sum(hidden_per_message(msgs, thinking, m)) for m in MODES}
    return min(MODES, key=lambda m: (abs(hidden_exact - totals[m]), MODES.index(m)))


def positions(day: Path, arms: set[str] | None = None) -> list[dict]:
    visible = json.loads((day / "visible_tokens.json").read_text())
    secrets = load_secrets()
    url, key = endpoint(secrets["ECONOCONTEXT_BASE_URL"]), secrets["AGENT_PLATFORM_API_KEY"]
    out = []
    for run in load_runs(day, arms):
        bodies = day / "bodies" / run.run_id
        calls = [c for c in run.calls if c["finish_reason"] not in RETRY_REASONS]
        if not bodies.is_dir() or not calls:
            continue
        req = [json.loads((bodies / f"{c['call_no']:04d}.request.json").read_text()) for c in calls]
        msgs = [b["messages"] for b in req]
        thinking: dict[str, int] = {}
        modes = []
        for c, b, m in zip(calls, req, msgs):           # thinking and each call's true state
            exact = c["prompt_tokens"] - (visible[f"{run.run_id}/{c['call_no']:04d}"] + OVERHEAD)
            modes.append(true_mode(m, thinking, exact))
            reply = json.loads((bodies / f"{c['call_no']:04d}.response.json").read_text())
            sig = signature(reply[0]["choices"][0]["message"]) if reply and reply[0].get("choices") else None
            if sig:
                thinking[sig_key(sig)] = c["reasoning_tokens"] or 0
        rr = {r["call"]: r for r in call_rereads(msgs, calls)}
        meter = HiddenMeter()
        for i, (c, m) in enumerate(zip(calls, msgs)):
            r = rr.get(i)
            if i and r and r["rewrite"]:
                prev = msgs[i - 1]
                j, a = first_change(prev, m), appended_start(prev, m, True)
                hid_cur = hidden_per_message(m, thinking, modes[i])
                hid_prev = hidden_per_message(prev, thinking, modes[i - 1])
                shared = shared_text(prev[j], m[j]) if j < len(prev) and j < len(m) else ""
                head = m[:j] + ([{**m[j], "content": shared}] if shared else [])
                vis_j = count(url, key, to_native({**req[i], "messages": head})) + OVERHEAD
                vis_a = count(url, key, to_native({**req[i], "messages": m[:a]})) + OVERHEAD
                P, A = vis_j + sum(hid_cur[:j]), vis_a + sum(hid_cur[:a])
                P_prev = vis_j + sum(hid_prev[:j])
                c_prev, c_now = calls[i - 1]["cached_tokens"] or 0, c["cached_tokens"] or 0
                q = edit_quote(prev, m[:a], c_prev, k=meter.k, count=default_count,
                               thinking=thinking, mode=meter.mode)
                appended = c["prompt_tokens"] - A
                extra = max(0, (c["uncached_tokens"] or 0) - appended)
                edit_caused = max(0, min(A, c_prev) - max(c_now, reachable_prefix(P)))
                lost_prefix = (max(0, min(P, A, c_prev) - c_now) if P < CACHE_MIN_TOKENS else 0)
                out.append(dict(
                    run=run.run_id, call=i, call_no=c["call_no"], first_msg=j, appended_msg=a,
                    gap_s=r["gap_s"], c_prev=c_prev, c_now=c_now, P_exact=P, A_exact=A,
                    p_live=q.prefix_tokens_p if q else None, R=q.R if q else None,
                    R_likely=q.R_likely if q else None,
                    R_exact=max(0, c_prev - reachable_prefix(P_prev)), extra=extra,
                    edit_caused=edit_caused, lost_prefix=lost_prefix,
                    post_edit_miss=extra - edit_caused, below_min=P < CACHE_MIN_TOKENS,
                    violation=bool(q and q.R is not None and edit_caused > q.R),
                    healthy=c_prev > 0 and c_now > 0, mode_prev=modes[i - 1], mode_now=modes[i]))
            meter.observe(m, c["prompt_tokens"], default_count(m), thinking)
    return out


def report(rows: list[dict]) -> str:
    perr = [abs(r["p_live"] - r["P_exact"]) for r in rows if r["p_live"] is not None]
    viol = [r for r in rows if r["violation"]]
    healthy = [r for r in rows if r["healthy"]]
    herr = [abs(r["R"] - r["edit_caused"]) for r in healthy if r["R"] is not None]
    lines = ["# Rewrites: exact positions and the edit-caused re-read", "",
             f"Rewrites: {len(rows)}",
             f"First changed position, live estimate p vs exact P: median |p - P| "
             f"{statistics.median(perr):.0f} tokens" if perr else "no positions",
             f"Bound violations (edit-caused re-read > R): {len(viol)} of {len(rows)}",
             f"R_likely exceeded: {sum(r['R_likely'] is not None and r['edit_caused'] > r['R_likely'] for r in rows)} "
             f"of {len(rows)}",
             f"Healthy cache before and after (both calls hit): {len(healthy)}; there median "
             f"|R - edit-caused| {statistics.median(herr):.0f} tokens, |R_likely - edit-caused| "
             f"{statistics.median([abs(r['R_likely'] - r['edit_caused']) for r in healthy]):.0f}"
             if herr else "no healthy rewrites",
             f"Totals: extra uncached {sum(r['extra'] for r in rows):,} = edit-caused "
             f"{sum(r['edit_caused'] for r in rows):,} (of which lost prefix below the "
             f"{CACHE_MIN_TOKENS:,}-token cache minimum {sum(r['lost_prefix'] for r in rows):,}) "
             f"+ post-edit misses {sum(r['post_edit_miss'] for r in rows):,}",
             "", "| Run | Call | Gap s | c before | c after | p live | P exact | A exact | R | R likely | R exact | "
             "Edit-caused | Post-edit misses | Violation | Healthy | State before→after |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['run']} | {r['call']} | {r['gap_s']:.0f} | {r['c_prev']} | {r['c_now']} | "
                     f"{r['p_live']} | {r['P_exact']} | {r['A_exact']} | {r['R']} | {r['R_likely']} | {r['R_exact']} | "
                     f"{r['edit_caused']} | {r['post_edit_miss']} | {'YES' if r['violation'] else ''} | "
                     f"{'yes' if r['healthy'] else ''} | {r['mode_prev']}→{r['mode_now']} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("day", type=Path)
    ap.add_argument("--arms", default=None)
    args = ap.parse_args()
    rows = positions(args.day, set(args.arms.split(",")) if args.arms else None)
    (args.day / "rewrite_positions.json").write_text(json.dumps(rows, indent=1))
    text = report(rows)
    (args.day / "rewrite_positions.md").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
