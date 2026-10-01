"""Does the cost model explain what worker placement cost? An offline replay ($0).

Why it exists: the first placement model chose RESUME in wres4 and was wrong (RESUME
cost 1.65x FRESH). Before any new model is trusted live, it must reproduce the
recorded runs. From the Agent DB and the gateway's captured request bodies this prints:

  1. the meter against the ledger, on every recorded call
  2. the cache models against the billed read/write split, per run
     (explicit: Claude Code; implicit: Gemini, with its hit rate fitted)
  3. the worker loop shape per harness (shared prefix, first-call tail, growth, output)
  4. the wctl4 (FRESH) and wres4 (RESUME) follow-ups: billed cost, the model's cost
     with the observed call count, what it would have chosen before the decision, and
     how many calls RESUME needed to save to win
  5. line overlap: how much of what the follow-up read the idle worker already held

Run: .venv/bin/python harness/explain_placement.py [--control wctl4] [--resume wres4]
"""

import argparse
import hashlib
import json
import re
import statistics
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from econocontext.config import load  # noqa: E402
from econocontext.costmodel import bash_reads  # noqa: E402
from econocontext.costmodel.meter import (ExplicitCache, ImplicitCache, Loop,  # noqa: E402
                                          fit_implicit, price_call)
from econocontext.costmodel.options import Shape, break_even_calls_saved, fresh, resume  # noqa: E402
from econocontext.pricing import ledger  # noqa: E402
from econocontext.pricing.rates import ratios  # noqa: E402
from econocontext.store.db import AgentDB  # noqa: E402
from econocontext.tokens import count_tokens  # noqa: E402
from omnigent_layer import anthropic_wire, observe  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CFG = load(ROOT / "config")
HANDBACK = "SubagentHandback"


def run_id(db, label: str) -> str:
    return db.rows("SELECT run_id FROM runs WHERE run_id LIKE ? ORDER BY started_at DESC LIMIT 1",
                   (label + ":%",))[0][0]


def card_for(model: str):
    name = model.split("/")[-1]
    return next((c for (_, m), c in CFG.cards.items() if m == name), None)


def calls(db, run: str) -> list[dict]:
    """A run's model calls in order, with their billed split and what they asked for."""
    out = []
    for r in db.rows("SELECT s.started_at t, s.name model, s.metadata m, o.* FROM runtime_spans s "
                     "JOIN outcomes o ON o.outcome_id=s.native_id WHERE s.run_id=? AND s.kind='model' "
                     "ORDER BY s.started_at", (run,)):
        m = json.loads(r["m"] or "{}")
        f, rd, w, w1 = (r["uncached_input"] or 0, r["cache_read"] or 0, r["cache_write"] or 0,
                        r["cache_write_1h"] or 0)
        out.append(dict(t=r["t"], model=r["model"], key=m.get("context_key"), hash=m.get("request_hash"),
                        F=f, R=rd, W=w + w1, O=r["output"] or 0, P=f + rd + w + w1, nu=r["cost_nu"],
                        usd=r["cost_usd"], complete=r["cost_complete"],
                        tools=[c["name"] for c in m.get("response_calls") or []], row=r))
    return out


def meter_check(db) -> tuple[int, float]:
    """Every complete recorded call priced again through the meter."""
    n, worst = 0, 0.0
    for c in (c for (run,) in db.rows("SELECT run_id FROM runs") for c in calls(db, run)):
        card, r = card_for(c["model"] or ""), c["row"]
        if not c["complete"] or card is None or c["nu"] is None:
            continue
        from econocontext.types import ProviderUsage
        usage = ProviderUsage(r["uncached_input"], r["cache_read"], r["cache_write"], r["output"],
                              cache_write_1h=r["cache_write_1h"] or 0,
                              cache_write_applicable=r["cache_write"] is not None)
        nu, _, _, _ = ledger.cost(usage, card, date.fromisoformat(r["created_at"][:10]))
        worst = max(worst, abs(nu - c["nu"]) / max(c["nu"], 1))
        n += 1
    return n, worst


def loops(run_calls: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for c in run_calls:
        out.setdefault(c["key"], []).append(c)
    return out


def split_error(run_calls: list[dict], cache, rates) -> tuple[float, float]:
    """A run's cost with each call's read/write split predicted from the prompt sizes
    alone, against the billed cost. The first call of each loop keeps its billed read
    (what it found cached is the shared prefix, measured elsewhere)."""
    predicted = actual = 0.0
    for loop in loops(run_calls).values():
        seen: list[int] = []
        for c in loop:
            if not seen:
                read = c["R"]
            elif isinstance(cache, ExplicitCache):
                read = max((p for p in seen if p <= c["P"]), default=0)
            else:
                read = cache.read(seen[-1])
            write = c["P"] - read - c["F"] if isinstance(cache, ExplicitCache) else 0
            fresh_in = c["F"] if isinstance(cache, ExplicitCache) else c["P"] - read
            predicted += (fresh_in + read * rates.cache_read_ratio + max(write, 0) * rates.cache_write_ratio
                          + c["O"] * rates.output_ratio)
            actual += price_call_of(c, rates)
            seen.append(c["P"])
    return predicted, actual


def price_call_of(c: dict, rates) -> float:
    from econocontext.types import ProviderUsage
    r = c["row"]
    return price_call(ProviderUsage(r["uncached_input"], r["cache_read"], r["cache_write"], r["output"],
                                    cache_write_1h=r["cache_write_1h"] or 0,
                                    cache_write_applicable=r["cache_write"] is not None), rates)


# -- request bodies: what each loop read ---------------------------------------------

def bodies() -> dict[str, Path]:
    return {hashlib.sha256(f.read_bytes()).hexdigest(): f
            for f in (ROOT / "logs" / "gateway" / "bodies").iterdir()}


def pairs(body: dict) -> list[dict]:
    """Every tool call in a request's history with its result, in order (observe.py's shape)."""
    msgs = [m for m in body.get("messages") or [] if isinstance(m.get("content"), list)]
    uses = {b["id"]: b for m in msgs for b in m["content"] if b.get("type") == "tool_use"}
    return [{"id": b["tool_use_id"], "name": uses[b["tool_use_id"]]["name"],
             "args": uses[b["tool_use_id"]].get("input") or {},
             "text": anthropic_wire._text(b.get("content")), "error": bool(b.get("is_error"))}
            for m in msgs for b in m["content"]
            if b.get("type") == "tool_result" and b.get("tool_use_id") in uses]


def lines_read(results: list[dict], workdir: str) -> dict[str, list[tuple[int, int]]]:
    held: dict[str, list[tuple[int, int]]] = {}
    for e in observe.evidence_events(results, anthropic_wire.TOOLS, workdir, 0):
        m = e.ref and re.match(r"^L(\d+)-(\d+)$", e.ref.range)
        if m:
            held.setdefault(e.source_key, []).append((int(m.group(1)), int(m.group(2))))
    return held


def first_prompt(body: dict) -> str:
    first = next(m for m in body["messages"] if m["role"] == "user")
    blocks = first["content"] if isinstance(first["content"], list) else [{"type": "text", "text": first["content"]}]
    texts = [b["text"] for b in blocks if b.get("type") == "text" and "<system-reminder>" not in b["text"]]
    return texts[-1] if texts else ""


# -- worker loops -------------------------------------------------------------------

def phases(loop: list[dict]) -> list[list[dict]]:
    """A worker loop cut into its tasks: each ends with a SubagentHandback. Calls after
    a handback that ask for nothing (Claude Code's closing call) belong to no task."""
    out, current = [], []
    for c in loop:
        if not current and not c["tools"] and out:
            continue  # the closing call after a handback
        current.append(c)
        if HANDBACK in c["tools"]:
            out.append(current)
            current = []
    return out + ([current] if current else [])


def as_loop(phase: list[dict]) -> Loop:
    """A recorded phase as the meter's Loop: its first call's cached and new tokens, the
    mean new tokens per later call, and the mean output."""
    first, later = phase[0], phase[1:]
    return Loop(cached=first["R"], first=first["W"] + first["F"], calls=len(phase),
                growth=statistics.fmean(c["W"] + c["F"] for c in later) if later else 0.0,
                output=statistics.fmean(c["O"] for c in phase))


def nu(phase: list[dict]) -> float:
    return sum(c["nu"] for c in phase)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--control", default="wctl4", help="the run where the follow-up got a new worker")
    p.add_argument("--resume", default="wres4", help="the run where the follow-up resumed a worker")
    a = p.parse_args()
    db = AgentDB(ROOT / "data" / "econocontext.sqlite3")
    index = bodies()
    claude = ratios(card_for("claude-sonnet-5"), date(2026, 9, 30))
    gemini = ratios(card_for("gemini-3.6-flash"), date(2026, 9, 30))

    n, worst = meter_check(db)
    print(f"1. meter vs ledger: {n} recorded calls, largest relative difference {worst:.2e}")

    runs = {r["run_id"]: r["host"] for r in db.rows("SELECT run_id, host FROM runs")}
    by_provider: dict[str, list[list[dict]]] = {"anthropic": [], "gemini": []}
    for run in runs:
        cs = [c for c in calls(db, run) if c["complete"] and card_for(c["model"] or "")]
        if cs:
            by_provider["anthropic" if "claude" in cs[0]["model"] else "gemini"].append(cs)
    implicit = fit_implicit([(prev["P"], c["R"]) for cs in by_provider["gemini"]
                             for loop in loops(cs).values() for prev, c in zip(loop, loop[1:])])
    print("2. cache split, predicted from prompt sizes vs billed (NU):")
    for name, provider, cache, rates in (
            ("claude code, explicit", "anthropic", ExplicitCache(), claude),
            (f"gemini, implicit (miss {implicit.miss:.2f}, lag {implicit.lag:.0f})", "gemini", implicit, gemini)):
        pairs_ = [split_error(cs, cache, rates) for cs in by_provider[provider]]
        errors = sorted(abs(p / a - 1) for p, a in pairs_)
        total = sum(p for p, _ in pairs_) / sum(a for _, a in pairs_) - 1
        print(f"   {name}: {len(errors)} runs, all runs together {total:+.2%}; per run median "
              f"|error| {statistics.median(errors):.2%}, largest {errors[-1]:.2%}")

    ctl, res = run_id(db, a.control), run_id(db, a.resume)
    workers = {run: [dict(w) for w in db.rows("SELECT * FROM claude_workers WHERE run_id=? ORDER BY "
                                              "started_at", (run,))] for run in (ctl, res)}
    ctl_loops, res_loops = loops(calls(db, ctl)), loops(calls(db, res))
    fresh_phase = phases(ctl_loops[workers[ctl][1]["context_key"]])[0]
    first_phase, resumed_phase = phases(res_loops[workers[res][0]["context_key"]])[:2]
    earlier = [phases(ctl_loops[workers[ctl][0]["context_key"]])[0], first_phase]

    def body(c):
        return json.loads(index[c["hash"]].read_bytes())

    new_first = [ph[0] for ph in earlier[1:] + [fresh_phase]]  # new workers with a warm shared prefix
    tails = [c["W"] + c["F"] - count_tokens(first_prompt(body(c))) for c in new_first]
    task = count_tokens(first_prompt(body(fresh_phase[0])))  # the follow-up's Agent prompt
    shape = Shape(shared_prefix=statistics.fmean(c["R"] for c in new_first),
                  first_tail=statistics.fmean(tails),
                  growth=statistics.fmean(c["W"] + c["F"] for ph in earlier for c in ph[1:]),
                  output=statistics.fmean(c["O"] for ph in earlier for c in ph),
                  resume_tail=resumed_phase[0]["W"] + resumed_phase[0]["F"] - task)
    print(f"3. Claude Code Explore worker shape (from the phase-1 loops): shared prefix "
          f"{shape.shared_prefix:.0f}, first-call tail {shape.first_tail:.0f} + task, growth "
          f"{shape.growth:.0f}/call, output {shape.output:.0f}/call, resumed first-call tail "
          f"{shape.resume_tail:.0f} + task; phase-1 calls {[len(ph) for ph in earlier]}")

    cache = ExplicitCache()
    print("4. the follow-up task:")
    for name, ph in (("FRESH  (new worker, wctl4) ", fresh_phase), ("RESUME (worker 1, wres4)  ", resumed_phase)):
        model = cache.price(as_loop(ph), claude)
        print(f"   {name} billed {nu(ph):8.0f} NU ${nu(ph) * claude.usd_per_nu:.4f} in {len(ph)} calls; "
              f"model with that N {model:8.0f} NU ({model / nu(ph) - 1:+.1%})")
    history = resumed_phase[0]["R"]
    n_hat = statistics.median(len(ph) for ph in earlier)
    f_loop = fresh(shape, task, n_hat)
    r_loop = resume(shape, history, True, task, n_hat)
    fp, rp = cache.price(f_loop, claude), cache.price(r_loop, claude)
    saved = break_even_calls_saved(f_loop, r_loop, cache, claude)
    print(f"   before the decision (same forecast N={n_hat:g} for both): FRESH {fp:.0f} NU, "
          f"RESUME {rp:.0f} NU (warm history {history}) -> {'FRESH' if fp <= rp else 'RESUME'}; "
          f"RESUME wins only if it needs {saved:.1f} fewer calls")
    for row in db.rows("SELECT run_id, candidates, chosen FROM decisions WHERE run_id IN (?,?) AND "
                       "intercept='plan_dispatch' ORDER BY created_at", (ctl, res)):
        costs = {k: round(v["prepare"] + v["work"] + v["integrate"] + v["leaves_behind"])
                 for k, v in json.loads(row["candidates"]).items()}
        print(f"   the first model, logged in {row['run_id'].split(':')[0]}: {costs} -> {row['chosen']}")

    work = {run: str(ROOT / "data" / "work" / re.sub(r"[^\w.-]", "_", run)) for run in (ctl, res)}
    held_ctl = lines_read(pairs(body(earlier[0][-1])), work[ctl])
    read_fresh = lines_read(pairs(body(fresh_phase[-1])), work[ctl])
    held_res = lines_read(pairs(body(first_phase[-1])), work[res])
    done = {u["id"] for u in pairs(body(first_phase[-1]))}
    read_resumed = lines_read([u for u in pairs(body(resumed_phase[-1])) if u["id"] not in done], work[res])
    for name, held, read in (("wctl4: the new worker's reads, held by the idle worker", held_ctl, read_fresh),
                             ("wres4: the resumed worker's new reads, held already", held_res, read_resumed)):
        share = bash_reads.line_overlap(held, read)
        total = sum(bash_reads.count(r) for r in read.values())
        print(f"5. {name}: {share:.0%} of {total} lines")


if __name__ == "__main__":
    main()
