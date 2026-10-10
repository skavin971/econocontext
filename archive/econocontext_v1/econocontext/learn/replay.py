"""Counterfactual replay: what would EconoContext have chosen, knowing what happened?

Why it exists: before letting the optimizer change a live run, score it offline on
finished runs. For every tool result a run admitted (learn/labels.py must have run):

1. Re-run the current planner and optimizer with the true H (`h_actual`) and a p:
     oracle     p = 1 if the result was needed again, else 0
     empirical  p = p_hat from *other* runs (learn/predictors.py)
2. Score the chosen option against what actually happened, in NU:
     KEEP_FULL  full + full x H x r
     POINTER    pointer + pointer x H x r
                + if needed: full (re-read) + full x (calls after the first need) x r
                  + the extra model call that re-sends the context (its prompt x r)
   r = 1 in "model" units (the cost model ignores the cache), and
   r = (1 - s) + s x cache_read_ratio when "cache-adjusted", s = this run's observed
   cache-read share. The saving is KEEP_FULL minus the chosen option.
3. An oracle bound for pointing a result out *later*: after its last need, keeping it
   costs (full - pointer) x remaining calls x r, against a one-time cache break of the
   tokens after it (priced at (1 - cache_read_ratio)).

What it must never do: change the Agent DB. It only reads.
"""

import json
from datetime import date

from ..assembler.assembler import pointer_text
from ..config import Config
from ..optimizer.optimizer import select
from ..planner import planner
from ..pricing.rates import ratios
from ..store.db import AgentDB
from ..tokens import count_tokens
from ..types import AgentNode, Intercept, PlanContext


def _cache_share(db: AgentDB, run_id: str) -> float:
    r = db.rows("SELECT COALESCE(SUM(uncached_input),0) u, COALESCE(SUM(cache_read),0) c "
                "FROM outcomes WHERE run_id=?", (run_id,))[0]
    return r["c"] / (r["u"] + r["c"]) if r["u"] + r["c"] else 0.0


def _truth(option: str, full: int, ptr: int, h: int, needs: list[int], arrived: int, end: int,
           r: float, prompt_at: list[int]) -> float:
    if option != "POINTER":
        return full + full * h * r
    cost = ptr + ptr * h * r
    later = [n for n in needs if n >= arrived]
    if later:
        extra_call = prompt_at[min(later[0], len(prompt_at) - 1)] * r if prompt_at else 0
        cost += full + full * max(0, end - later[0]) * r + extra_call
    return cost


def replay_run(db: AgentDB, run_id: str, config: Config, allow: set[str], history=None) -> dict:
    cfg = config.raw
    rates = ratios(config.card, date.today())
    s = _cache_share(db, run_id)
    r_adj = (1 - s) + s * rates.cache_read_ratio
    allowlist = {**cfg["allowlist"], **{name: True for name in allow}}
    ends = {x["agent_id"]: x["n"] for x in db.rows(
        "SELECT agent_id, COUNT(*) n FROM outcomes WHERE run_id=? GROUP BY agent_id", (run_id,))}
    prompts = {}
    for x in db.rows("SELECT agent_id, COALESCE(uncached_input,0)+COALESCE(cache_read,0) p "
                     "FROM outcomes WHERE run_id=? ORDER BY created_at", (run_id,)):
        prompts.setdefault(x["agent_id"], []).append(x["p"])
    rows = db.rows(
        "SELECT d.subject_id, d.agent_id, h.h_actual, l.tool_name, l.arrived_call, l.needed_calls "
        "FROM decisions d JOIN labels h ON h.run_id = d.run_id AND h.kind='decision' "
        "AND h.subject_id = d.decision_id JOIN labels l ON l.run_id = d.run_id AND "
        "l.kind='result' AND l.subject_id = d.subject_id "
        "WHERE d.run_id=? AND d.intercept='admit_tool_result'", (run_id,))
    out = dict(run_id=run_id, results=0, cache_share=round(s, 3),
               changed={"oracle": 0, "empirical": 0},
               saving_nu={"oracle": 0.0, "empirical": 0.0},
               saving_nu_cache_adjusted={"oracle": 0.0, "empirical": 0.0},
               later_pointer_oracle_nu_cache_adjusted=0.0, rows=[])
    for row in rows:
        segment = db.segment(row["subject_id"])
        if segment is None:
            continue
        full, h = segment.tokens, max(1, row["h_actual"])
        ptr = count_tokens(pointer_text(segment, "<path>", cfg["planner"]["pointer_preview_lines"]))
        needs, arrived = json.loads(row["needed_calls"]), row["arrived_call"]
        end = ends.get(row["agent_id"], arrived + h)
        needed = bool(needs)
        record = dict(tool=row["tool_name"], tokens=full, h=h, needed_again=needed)
        for mode in ("oracle", "empirical"):
            p = float(needed) if mode == "oracle" or history is None else \
                history.p_hat(row["tool_name"], 0)[0]
            ctx = PlanContext(run_id=run_id, agent=AgentNode(row["agent_id"], None, None),
                              intercept=Intercept.ADMIT_TOOL_RESULT, window=[],
                              window_max_tokens=cfg["limits"]["window_max_tokens"],
                              current_versions={}, constraints=config.constraints,
                              allowlist=allowlist, rates=rates, remaining_turns=h)
            agent_prompts = prompts.get(row["agent_id"], [])
            window = agent_prompts[min(arrived, len(agent_prompts) - 1)] if agent_prompts else 0
            chosen = select(planner.for_tool_result(ctx, cfg, segment, ptr, p, window), ctx,
                            config.constraints, cfg).chosen.name
            record[mode] = chosen
            if chosen != "KEEP_FULL":
                out["changed"][mode] += 1
            for key, r in (("saving_nu", 1.0), ("saving_nu_cache_adjusted", r_adj)):
                out[key][mode] += (
                    _truth("KEEP_FULL", full, ptr, h, needs, arrived, end, r, agent_prompts)
                    - _truth(chosen, full, ptr, h, needs, arrived, end, r, agent_prompts))
        if needs:  # oracle: point it out right after its last need
            edit_at = needs[-1] + 1
            remaining = max(0, end - edit_at)
            agent_prompts = prompts.get(row["agent_id"], [])
            suffix = (agent_prompts[edit_at] - agent_prompts[arrived]
                      if edit_at < len(agent_prompts) and arrived < len(agent_prompts) else 0)
            gain = ((full - ptr) * remaining * r_adj
                    - max(0, suffix) * (1 - rates.cache_read_ratio) * s)
            out["later_pointer_oracle_nu_cache_adjusted"] += max(0.0, gain)
        out["results"] += 1
        out["rows"].append(record)
    for key in ("saving_nu", "saving_nu_cache_adjusted"):
        out[key] = {k: round(v) for k, v in out[key].items()}
    out["later_pointer_oracle_nu_cache_adjusted"] = round(out["later_pointer_oracle_nu_cache_adjusted"])
    actual = db.rows("SELECT COALESCE(SUM(cost_nu),0) nu FROM outcomes WHERE run_id=?", (run_id,))[0]
    out["actual_run_nu"] = round(actual["nu"])
    return out
