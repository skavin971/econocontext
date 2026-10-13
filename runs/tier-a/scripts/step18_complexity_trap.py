"""Step 18: The Complexity Trap's released trajectories, replayed with our engine. Offline, no model calls.
FIXED-TRAJECTORY ESTIMATES on the canonical track.

Data (outside our repo, never committed): ~/econo/ctrap/data/, three SWE-agent archives of the HuggingFace dataset
JetBrains-Research/the-complexity-trap (Apache-2.0): Qwen3-Coder-480B-A35B-Instruct-FP8 on SWE-bench Verified (500
instances): raw agent (baseline_raw), observation masking (baseline_N_1_M_10: last_n_observations n = 10, polling 1)
and LLM-Summary (turn-summaries N_21_M_10_openhands). Their paper's "~52% cheaper" is this model's masking arm:
mean instance cost $0.610 against $1.288 raw (auxiliary-data/bootstrapped_cis.csv of their repository).
The archives are streamed, never extracted (14 GB uncompressed).

Per instance and arm, every call as it was sent: each agent turn's `messages` (the prompt after their history
processors, masked observations included) with its `response`, and, in the summary arm, each summarizer call
(`summaries[i].context`, its `summary` as output), placed just before the first agent turn whose prompt contains
that summary. Messages are tokenized with our Qwen tokenizer (measure/tokens.py) once per distinct message (content
plus tool-call names and arguments, plus a 5-token wrapper); a call's prompt is its messages in order plus a 3-token
generation prompt; G is the response (or summary) in Qwen tokens. A vLLM-style prefix cache per instance, at message
granularity (16-token rounding, last-token rule; steps 15-17), gives R.

Priced four ways, per call:
  - tokens sent: P;
  - dollars without a cache-read discount: P x input + G x output;
  - dollars with their cache-read price: (P - R) x input + R x cached input + G x output;
    and the same with cache reads at 10% of input (step 17's card);
    using their price card for this model (notebooks/posthoc_compute_qwen_costs.py, "coder", $ per 1M tokens, tier by
    context: < 32k: 1 / 5 / 0.4; 32k-128k: 1.8 / 9 / 0.72; 128k-256k: 3 / 15 / 1.2; 256k-1M: 6 / 60 / 2.4);
  - FLOPs: Eq. 9 (measure.flops.eq9) with our Qwen constants.
Also their own figures from each trajectory (info.model_stats: instance_cost and its tokens), as a check.
Counterfactual (same trajectory): their masking rule applied by us to the raw arm's own prompts (observations other
than the first and the last 10 become "Old environment output: (N lines omitted)"), separating the context effect from
the change in behaviour between runs.

Use: PYTHONPATH=. .venv/bin/python runs/tier-a/scripts/step18_complexity_trap.py [--workers 5] [--limit N]
Output: runs/tier-a/step18/instances.csv (instance ids and numbers only)
"""

import argparse
import csv
import hashlib
import json
import sys
import tarfile
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from measure.flops import eq9  # noqa: E402
from measure.models import MEASURED, load  # noqa: E402
from measure.tokens import count_text  # noqa: E402

DATA = Path.home() / "econo" / "ctrap" / "data"
OUT = ROOT / "runs" / "tier-a" / "step18"
PREFIX = "local-Qwen3-Coder_480B_A35B_Instruct_FP8-"
ARMS = {"raw": "baseline_raw.0-verified-500", "masking": "baseline_N_1_M_10.0-verified-500",
        "summary": "t_0.8-turn-summaries-t_0-N_21_M_10_openhands.0-verified-500"}
WRAP, GEN, BLOCK, KEEP = 5, 3, 16, 10
CARD = [(32_000, 1.0, 5.0, 0.4), (128_000, 1.8, 9.0, 0.72), (256_000, 3.0, 15.0, 1.2), (1_000_000, 6.0, 60.0, 2.4)]
K = load(MEASURED)


def tier(P):
    return next((c for c in CARD if P < c[0]), CARD[-1])


def text_of(m: dict) -> str:
    c = m.get("content")
    if isinstance(c, list):
        c = "".join(x.get("text", "") for x in c if isinstance(x, dict))
    calls = "".join(f"{(t.get('function') or {}).get('name', '')} {(t.get('function') or {}).get('arguments', '')}"
                    for t in m.get("tool_calls") or [])
    return (c or "") + calls


def masked(messages: list[dict]) -> list[dict]:
    """SWE-agent's LastNObservations (n = 10, polling 1): every observation but the first and the last 10 omitted."""
    obs = [i for i, m in enumerate(messages) if m.get("message_type") == "observation"]
    omit = set(obs[1:max(0, len(obs) - KEEP)])
    return [{**m, "content": f"Old environment output: ({len(text_of(m).splitlines())} lines omitted)"} if i in omit else m
            for i, m in enumerate(messages)]


def price(calls: list[tuple[list[dict], str]]) -> dict:
    """calls: (messages, output text) in order. Totals under the four prices."""
    tokens, seen = {}, set()
    res = {"calls": len(calls), "sent": 0, "new": 0, "G": 0, "usd": 0.0, "usd_10": 0.0, "usd_nodisc": 0.0, "F": 0.0}
    for messages, output in calls:
        h, cum, R, matched, hashes = 0, 0, 0, True, []
        for m in messages:
            text = text_of(m)
            key = hashlib.blake2b(f"{m.get('role')}\x00{text}".encode(), digest_size=12).digest()
            if key not in tokens:
                tokens[key] = count_text(text, MEASURED) + WRAP
            h = hash((h, key))
            cum += tokens[key]
            hashes.append(h)
            if matched and h in seen:
                R = cum
            else:
                matched = False
        P = cum + GEN
        R = min(BLOCK * (R // BLOCK), BLOCK * ((P - 1) // BLOCK))
        seen.update(hashes)
        G = count_text(output or "", MEASURED)
        _, i_in, i_out, i_cache = tier(P)
        res["sent"] += P
        res["new"] += P - R
        res["G"] += G
        res["usd"] += ((P - R) * i_in + R * i_cache + G * i_out) / 1e6
        res["usd_10"] += ((P - R) * i_in + R * 0.1 * i_in + G * i_out) / 1e6
        res["usd_nodisc"] += (P * i_in + G * i_out) / 1e6
        res["F"] += sum(eq9(P, R, G, K["C_token"], K["C_attn"]))
    return res


def analyse(job: tuple[str, str, bytes]) -> dict:
    arm, instance, raw = job
    try:
        d = json.loads(raw)
    except ValueError:                                    # an empty or truncated .traj file: counted, not priced
        return {"arm": arm, "instance": instance, "unreadable": True, "bytes": len(raw)}
    turns = [t for t in d["trajectory"] if t.get("messages")]
    calls = [(t["messages"], t.get("response") or "") for t in turns]
    for s in d.get("summaries") or []:
        key = (s.get("summary") or "")[:200]
        at = next((i for i, (msgs, _) in enumerate(calls) if key and any(key in text_of(m) for m in msgs)), len(calls))
        calls.insert(at, (s.get("context") or [], s.get("summary") or ""))
    row = {"arm": arm, "instance": instance, "unreadable": False, "bytes": len(raw), "turns": len(turns), "summaries": len(d.get("summaries") or []),
           "exit_status": (d.get("info") or {}).get("exit_status")}
    for k, v in price(calls).items():
        row[k] = v
    ms = (d.get("info") or {}).get("model_stats") or {}
    tok = ms.get("tokens") or {}
    row.update(their_cost=ms.get("instance_cost"), their_raw_input=tok.get("raw_input"),
               their_cached_input=tok.get("cached_input"), their_output=tok.get("output"))
    if arm == "raw":
        for k, v in price([(masked(t["messages"]), t.get("response") or "") for t in turns]).items():
            row[f"cf_{k}"] = v
    return row


def jobs(limit: int):
    for arm, suffix in ARMS.items():
        n = 0
        with tarfile.open(DATA / f"{PREFIX}{suffix}.tar.gz", "r|gz") as t:
            for m in t:
                if m.isfile() and m.name.endswith(".traj"):
                    yield arm, m.name.split("/")[-1][:-5], t.extractfile(m).read()
                    n += 1
                    if limit and n >= limit:
                        break


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--limit", type=int, default=0, help="first N instances per arm (testing)")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    rows, pending = [], set()
    with ProcessPoolExecutor(a.workers) as pool:              # bounded: trajectories are streamed, not all held
        for job in jobs(a.limit):
            pending.add(pool.submit(analyse, job))
            if len(pending) >= 3 * a.workers:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                rows += [x.result() for x in done]
        rows += [x.result() for x in pending]
    order = next(r for r in rows if not r["unreadable"] and r["arm"] == "raw")
    keys = sorted({k for r in rows for k in r}, key=lambda k: (k.startswith("cf_"), list(order).index(k) if k in order else 99))
    name = "instances.csv" if not a.limit else "instances-test.csv"
    with (OUT / name).open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} trajectories")


if __name__ == "__main__":
    main()
