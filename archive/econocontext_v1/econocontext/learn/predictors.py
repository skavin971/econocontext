"""Learned predictions from earlier labelled runs, instead of the fixed guesses in config.

Why it exists: H (calls an agent will still make) and p (whether a result is needed
again) drive the `leaves_behind` and re-read terms of every price. Here they come from
what earlier runs actually did (learn/labels.py):

  H_hat(role, t)  median of (total calls - t) over earlier agents of the same role
                  (root, worker, ...) that made more than t calls
  p_hat(tool, a)  among earlier results of this tool that went `a` calls without being
                  needed, the share that was needed again later; smoothed (k+1)/(n+2).
                  a = 0 is "needed again at all"

Leave-one-out: a task's own runs are never used to predict it. With no history the
config guess is used, and the source says so ("prior"), so it is always visible which.
What it must never do: write anything.
"""

import json
import statistics

from ..store.db import AgentDB


def role(agent_id: str) -> str:
    """'<run>:root' -> 'root'; '<run>:worker:<hash>' -> 'worker'."""
    parts = agent_id.split(":")
    return "root" if parts[-1] == "root" else (parts[-2] if len(parts) >= 3 else parts[-1])


class History:
    def __init__(self, db: AgentDB, exclude_instance: str | None = None,
                 default_turns: int = 18, prior_p: float = 0.3):
        self.default_turns, self.prior_p = default_turns, prior_p
        runs = [r["run_id"] for r in db.rows(
            "SELECT DISTINCT r.run_id FROM runs r JOIN labels l ON l.run_id = r.run_id "
            "WHERE r.instance_id IS NOT ? OR ? IS NULL", (exclude_instance, exclude_instance))]
        self.runs = runs
        shares = [r["c"] / (r["u"] + r["c"]) for r in (db.rows(
            "SELECT COALESCE(SUM(uncached_input),0) u, COALESCE(SUM(cache_read),0) c FROM outcomes "
            "WHERE run_id=?", (run_id,))[0] for run_id in runs) if r["u"] + r["c"]]
        self.cache_share = statistics.median(shares) if shares else 0.0  # before this run has calls
        self.totals: dict[str, list[int]] = {}      # role -> total calls per earlier agent
        self.results: dict[str, list[tuple[int, list[int], int]]] = {}  # tool -> (arrived, needed, end)
        for run_id in runs:
            ends = {r["agent_id"]: r["n"] for r in db.rows(
                "SELECT agent_id, COUNT(*) n FROM outcomes WHERE run_id=? GROUP BY agent_id",
                (run_id,))}
            for agent_id, n in ends.items():
                self.totals.setdefault(role(agent_id), []).append(n)
            for r in db.rows("SELECT agent_id, tool_name, arrived_call, needed_calls FROM labels "
                             "WHERE run_id=? AND kind='result'", (run_id,)):
                self.results.setdefault(r["tool_name"], []).append(
                    (r["arrived_call"], json.loads(r["needed_calls"]), ends.get(r["agent_id"], 0)))

    def h_hat(self, agent_id: str, calls_so_far: int) -> int:
        remaining = [total - calls_so_far for total in self.totals.get(role(agent_id), [])
                     if total > calls_so_far]
        if remaining:
            return max(1, int(statistics.median(remaining)))
        return max(1, self.default_turns - calls_so_far)  # no history for this point: the guess

    def p_hat(self, tool: str, age: int = 0) -> tuple[float, str]:
        """(p, source): the chance a result of `tool`, idle for `age` calls, is needed again."""
        items = self.results.get(tool) or [x for xs in self.results.values() for x in xs]
        if not items:
            return self.prior_p, "prior"
        reached = needed = 0
        for arrived, needs, end in items:
            # Reference points: arrival and each later need. The first one followed by
            # `age` quiet calls counts; it is a positive if a need comes after that quiet run.
            for ref in [arrived] + needs:
                later = [n for n in needs if n > ref]
                if ref + age <= end and not any(n <= ref + age for n in later):
                    reached += 1
                    needed += bool(later)
                    break
        if reached == 0:
            return self.prior_p, "prior"
        scope = tool if self.results.get(tool) else "all tools"
        return (needed + 1) / (reached + 2), f"history ({scope}: {needed}/{reached})"
