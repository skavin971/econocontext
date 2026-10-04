"""While the model never edits VIEW.md, EconoCLM-View's prompt equals CLM's. Offline.

  python -m econoclm.analysis.view_equivalence runs/<phase> [--arm raw]

Replays each recorded CLM trajectory (context_snapshots: the messages of every model
call) through ViewContextEnv's sync, call by call, as if the model never touched the
view: CLM's appended messages become `turn K` lines, and the view is rendered. At every
call the rendered prompt (after the protected prefix) must equal CLM's messages exactly
(json, key order ignored). Runs with no context edit are checked in full; runs with edits
up to their first edit (after that CLM's own rewrite has no View counterpart). The
system prompt differs only in the "Managing your context" section (checked separately:
agent.swap_section).
"""

import argparse
import copy
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

from ..arms.econo_view.env import ViewContextEnv
from ..arms.econo_view.view import fingerprint
from .common import load_runs


def env_for_replay(tmp: Path) -> ViewContextEnv:
    paths = SimpleNamespace(ctx_dir="/tmp/.live_ctx", ctx_file="/tmp/.live_ctx/LIVE_CTX_MAIN.txt",
                            state_dir="/tmp/.bash_ctx_state")
    budget = SimpleNamespace(strict_target=0, count=lambda m: 0, note_compaction=lambda: None)
    return ViewContextEnv(paths=paths, budget=budget, protect=2, obs_text=lambda n: None,
                          run_dir=tmp)


def check_run(snaps: list[list[dict]]) -> dict:
    env = env_for_replay(Path(tempfile.mkdtemp(prefix="view_eq_")))
    prev = None
    checked = 0
    for t, snap in enumerate(snaps):
        if prev is not None:
            p = [fingerprint(m) for m in prev]
            if [fingerprint(m) for m in snap[:len(prev)]] != p:
                return {"calls": len(snaps), "checked": checked, "stopped_at_edit": t, "mismatch": None}
        messages = copy.deepcopy(snap)
        env.sync(messages)
        if [fingerprint(m) for m in messages] != [fingerprint(m) for m in snap] or env.n_normalized:
            return {"calls": len(snaps), "checked": checked, "stopped_at_edit": None, "mismatch": t}
        checked += 1
        prev = snap
    return {"calls": len(snaps), "checked": checked, "stopped_at_edit": None, "mismatch": None,
            "turns": env.next_turn - 1}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("day", type=Path)
    ap.add_argument("--arm", default="raw")
    args = ap.parse_args()
    ok = True
    print(f"# View equivalence on recorded CLM trajectories ({args.day.name}, arm {args.arm})\n")
    print("| Run | Calls | Calls checked | Result |")
    print("|---|---|---|---|")
    for run in load_runs(args.day, {args.arm}):
        r = check_run(run.snapshots())
        if r["mismatch"] is not None:
            ok, res = False, f"MISMATCH at call {r['mismatch']}"
        elif r["stopped_at_edit"] is not None:
            res = f"identical up to CLM's first edit (call {r['stopped_at_edit']})"
        else:
            res = f"identical in full ({r.get('turns')} turns)"
        print(f"| {run.task} | {r['calls']} | {r['checked']} | {res} |")
    print(f"\n{'PASS' if ok else 'FAIL'}: rendered prompt == CLM's prompt at every checked call")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
