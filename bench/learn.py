"""Label finished runs, then replay them: Phase 1's labels and Phase 2's counterfactuals.

    .venv/bin/python bench/learn.py label  --label p1
    .venv/bin/python bench/learn.py replay --label p1 [--allow POINTER]
    .venv/bin/python bench/learn.py oracle --label g1

`label` writes what actually happened to the `labels` table (econocontext/learn/labels.py;
for runs with evidence, also learn/evidence_labels.py: which reads were repeated, and
whether they had to be).
`replay` asks what EconoContext would have chosen with the true H and needed-again, and
what that would have saved (econocontext/learn/replay.py). `oracle` bounds how many
model turns only fetched evidence (econocontext/oracle/evidence_schedule.py; needs runs
with evidence: Gemini CLI, econo arm). Offline: no model calls.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from econocontext import config  # noqa: E402
from econocontext.learn.evidence_labels import label_evidence  # noqa: E402
from econocontext.learn.labels import label_run  # noqa: E402
from econocontext.learn.predictors import History  # noqa: E402
from econocontext.learn.replay import replay_run  # noqa: E402
from econocontext.oracle.evidence_schedule import headroom, schedule  # noqa: E402
from econocontext.pricing.rates import ratios  # noqa: E402
from econocontext.store.db import AgentDB  # noqa: E402


def runs(db: AgentDB, label: str) -> list[dict]:
    return [dict(r) for r in db.rows("SELECT run_id, instance_id FROM runs WHERE run_id LIKE ? "
                                     "ORDER BY run_id", (f"{label}:%",))]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("command", choices=["label", "replay", "oracle"])
    p.add_argument("--label", required=True)
    p.add_argument("--allow", nargs="*", default=["POINTER"],
                   help="approximate operators replay may choose (default: POINTER)")
    p.add_argument("--db", default=str(ROOT / "data" / "econocontext.sqlite3"))
    a = p.parse_args()
    db = AgentDB(a.db)
    todo = runs(db, a.label)
    if not todo:
        raise SystemExit(f"no runs with label {a.label!r}")
    if a.command == "label":
        for run in todo:
            print(json.dumps(label_run(db, run["run_id"])))
            # after label_run, which replaces all of a run's labels
            print(json.dumps(label_evidence(db, run["run_id"])))
        return
    if a.command == "oracle":
        return oracle(db, todo)
    cfg = config.load(ROOT / "config", {"constraints": {"max_quality_risk": 0.2}})
    total = dict(results=0, actual=0, oracle=0, empirical=0, oracle_adj=0, empirical_adj=0, later=0)
    print(f"{'run':52} {'results':>7} {'changed o/e':>11} {'saved NU oracle':>16} "
          f"{'(cache-adj)':>11} {'empirical':>10} {'(cache-adj)':>11} {'later ptr':>9} {'run NU':>9}")
    for run in todo:
        out = replay_run(db, run["run_id"], cfg, set(a.allow),
                         History(db, exclude_instance=run["instance_id"]))
        s, adj = out["saving_nu"], out["saving_nu_cache_adjusted"]
        print(f"{run['run_id'][:52]:52} {out['results']:>7} "
              f"{out['changed']['oracle']:>5}/{out['changed']['empirical']:<5} "
              f"{s['oracle']:>16,} {adj['oracle']:>11,} {s['empirical']:>10,} "
              f"{adj['empirical']:>11,} {out['later_pointer_oracle_nu_cache_adjusted']:>9,} "
              f"{out['actual_run_nu']:>9,}")
        total["results"] += out["results"]
        total["actual"] += out["actual_run_nu"]
        for mode in ("oracle", "empirical"):
            total[mode] += s[mode]
            total[f"{mode}_adj"] += adj[mode]
        total["later"] += out["later_pointer_oracle_nu_cache_adjusted"]
    print(f"{'TOTAL':52} {total['results']:>7} {'':>11} {total['oracle']:>16,} "
          f"{total['oracle_adj']:>11,} {total['empirical']:>10,} {total['empirical_adj']:>11,} "
          f"{total['later']:>9,} {total['actual']:>9,}")
    print("\noracle = p from what actually happened; empirical = p_hat from the other tasks "
          "(leave-one-out).\ncache-adj = residency priced at the run's observed cache mix. "
          "'later ptr' = oracle bound for pointing a result out after its last use.\n"
          "A pipeline check on few tasks at temperature 1.0: not a savings claim.")


def oracle(db: AgentDB, todo: list[dict]) -> None:
    """Per run: model turns that only fetched evidence, and what removing them would save."""
    card = config.load(ROOT / "config").card
    usd_per_token = ratios(card, datetime.now(timezone.utc).date()).usd_per_nu
    print(f"{'run':52} {'calls':>5} {'strict':>6} {'mixed':>5} {'share':>6} "
          f"{'run $':>8} {'saved $':>8} {'share':>6}")
    total = dict(calls=0, strict=0, mixed=0, cost=0.0, saved=0.0)
    for run in todo:
        h = headroom(schedule(db, run["run_id"], usd_per_token))
        print(f"{run['run_id'][:52]:52} {h['calls']:>5} {h['strict']:>6} {h['mixed']:>5} "
              f"{h['share_of_calls'] or 0:>6.0%} {h['cost_usd']:>8.4f} "
              f"{h['estimated_saved_usd']:>8.4f} {h['share_of_cost'] or 0:>6.0%}")
        for k, v in (("calls", "calls"), ("strict", "strict"), ("mixed", "mixed"),
                     ("cost", "cost_usd"), ("saved", "estimated_saved_usd")):
            total[k] += h[v]
    print(f"{'TOTAL':52} {total['calls']:>5} {total['strict']:>6} {total['mixed']:>5} "
          f"{total['strict'] / total['calls'] if total['calls'] else 0:>6.0%} {total['cost']:>8.4f} "
          f"{total['saved']:>8.4f} {total['saved'] / total['cost'] if total['cost'] else 0:>6.0%}")
    print("\nstrict = turns that only read (files, searches) and said nothing: the upper bound on\n"
          "turns evidence supplied one call earlier could remove. mixed = reads plus text, not\n"
          "claimed. saved = strict turns' cost minus their evidence paid again as fresh input.")


if __name__ == "__main__":
    main()
