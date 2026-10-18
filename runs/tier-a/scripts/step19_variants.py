"""Step 19: cache-read price sweep and cache-aware masking variants, on The Complexity Trap's SWE-agent trajectories.
Offline, no model calls. FIXED-TRAJECTORY ESTIMATES on the canonical track (step 18's engine: Qwen tokenizer, a
prefix cache per instance at message granularity, Eq. 9 with our Qwen constants).

Models (archives in ~/econo/ctrap/data/, streamed, never written to the repo): Qwen3-Coder-480B (step 18), Qwen3-32B
(non-thinking) and Gemini 2.5 Flash (non-thinking); for each, the raw, masking (last_n_observations n = 10, polling 1)
and LLM-Summary (N = 21, M = 10) arms on SWE-bench Verified.

1. Price sweep, the arms as run: dollars = A + c x B per instance, A = uncached input x input price + output x output
   price, B = cached input x input price (cache reads at a fraction c of the input price). Price cards: Qwen3-Coder,
   their tiered card (input / output $ per 1M by context: < 32k 1 / 5; 32k-128k 1.8 / 9; 128k-256k 3 / 15; 256k-1M
   6 / 60); Qwen3-32B, their card 0.70 / 2.80; Gemini 2.5 Flash 0.30 / 2.50 (litellm 1.77 and Google's page).
2. Variants on the raw arm's own prompts (the same fixed trajectory; the raw agent's prompts are append-only, checked
   per turn, so turn t's prompt is the first L_t messages of the instance's history):
   masking (their rule, as a counterfactual), V1 block masking (their rule recomputed only every k turns: SWE-agent's
   polling = k, k in {5, 10, 20}), V2 age-1 stubbing (an observation becomes their placeholder "Old environment
   output: (N lines omitted)" once it is not the newest, unless the reply right after it referenced it), V2r (V2, and
   an observation a later reply needs while stubbed is restored in full at the tail of that prompt), V3 two-zone
   (append-only; when the prompt passes a cap, stub observations cheapest first, the least text after them, never the
   newest, down to 60% of the cap; caps: the masking arm's median and p90 prompt length in our tokens).
   The first observation (the task) is never stubbed. "Referenced" and "needed" use step 17's rule: a reply quotes 30+
   characters of the observation, or names a path, code-like identifier or multi-word proper name from it, that no
   other message before that reply contains. Info loss: replies that reference an observation stubbed in their prompt.
   Reported per variant, with raw and the masking and summary arms as run: tokens sent, FLOPs, dollars at cache reads of
   10%, 25% and 40% of input, and the median and p90 prompt length over calls.

Use: PYTHONPATH=. .venv/bin/python runs/tier-a/scripts/step19_variants.py [--model coder|qwen32b|gemini] [--workers 5]
     [--limit N]
Output: runs/tier-a/step19/<model>_instances.csv and <model>_lengths.json (numbers and instance ids only)
"""

import argparse
import csv
import hashlib
import json
import statistics
import sys
import tarfile
from collections import defaultdict
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import step16_waste as s16  # noqa: E402

from measure.flops import eq9  # noqa: E402
from measure.models import MEASURED, load  # noqa: E402
from measure.tokens import count_text  # noqa: E402

DATA = Path.home() / "econo" / "ctrap" / "data"
OUT = ROOT / "runs" / "tier-a" / "step19"
MODELS = {
    "coder": {"prefix": "local-Qwen3-Coder_480B_A35B_Instruct_FP8-",
              "arms": {"raw": "baseline_raw.0-verified-500", "masking": "baseline_N_1_M_10.0-verified-500",
                       "summary": "t_0.8-turn-summaries-t_0-N_21_M_10_openhands.0-verified-500"},
              "card": [(32_000, 1.0, 5.0), (128_000, 1.8, 9.0), (256_000, 3.0, 15.0), (10 ** 9, 6.0, 60.0)]},
    "qwen32b": {"prefix": "local-qwen3-32b-",
                "arms": {"raw": "baseline_raw.0-verified-500", "masking": "baseline_N_1_M_10.0-verified-500",
                         "summary": "t_0.8-turn-summaries-t_0-N_21_M_10_openhands.0-verified-500"},
                "card": [(10 ** 9, 0.70, 2.80)]},
    "gemini": {"prefix": "", "arms": {"raw": "gemini_2.5_flash-agent-t_0.8-baseline_raw.0-verified-500",
                                      "masking": "gemini_mask",
                                      "summary": "gemini_2.5_flash-agent-t_0.8-turn-summaries-t_0-N_21_M_10_openhands.0-verified-500"},
               "card": [(10 ** 9, 0.30, 2.50)]},
}
WRAP, GEN, BLOCK, KEEP, LOW_WATER = 5, 3, 16, 10, 0.6
K = load(MEASURED)


def text_of(m: dict) -> str:
    c = m.get("content")
    if isinstance(c, list):
        c = "".join(x.get("text", "") for x in c if isinstance(x, dict))
    calls = "".join(f"{(t.get('function') or {}).get('name', '')} {(t.get('function') or {}).get('arguments', '')}"
                    for t in m.get("tool_calls") or [])
    return (c or "") + calls


def stub_text(text: str) -> str:
    return f"Old environment output: ({len(text.splitlines())} lines omitted)"


def key_of(role: str, text: str) -> bytes:
    return hashlib.blake2b(f"{role}\x00{text}".encode(), digest_size=12).digest()


class Pricer:
    """Per call: P, R (prefix cache over keys), G; accumulates the run's totals."""

    def __init__(self, card):
        self.card, self.seen = card, set()
        self.r = {"calls": 0, "sent": 0, "new": 0, "F": 0.0, "A": 0.0, "B": 0.0}
        self.P = []

    def call(self, keys, toks, G):
        h, cum, R, matched, hashes = 0, 0, 0, True, []
        for k, n in zip(keys, toks):
            h = hash((h, k))
            cum += n
            hashes.append(h)
            if matched and h in self.seen:
                R = cum
            else:
                matched = False
        P = cum + GEN
        R = min(BLOCK * (R // BLOCK), BLOCK * ((P - 1) // BLOCK))
        self.seen.update(hashes)
        _, i_in, i_out = next(c for c in self.card if P < c[0])
        self.r["calls"] += 1
        self.r["sent"] += P
        self.r["new"] += P - R
        self.r["F"] += sum(eq9(P, R, G, K["C_token"], K["C_attn"]))
        self.r["A"] += ((P - R) * i_in + G * i_out) / 1e6
        self.r["B"] += R * i_in / 1e6
        self.P.append(P)
        return P


def as_run(d: dict, card) -> tuple[dict, list[int]]:
    """An arm exactly as it ran: every agent turn's prompt, and the summarizer's calls before the turn that shows them."""
    turns = [t for t in d["trajectory"] if t.get("messages")]
    calls = [(t["messages"], t.get("response") or "") for t in turns]
    for s in d.get("summaries") or []:
        key = (s.get("summary") or "")[:200]
        at = next((i for i, (msgs, _) in enumerate(calls) if key and any(key in text_of(m) for m in msgs)), len(calls))
        calls.insert(at, (s.get("context") or [], s.get("summary") or ""))
    pr, cache = Pricer(card), {}
    for msgs, out in calls:
        keys, toks = [], []
        for m in msgs:
            t = text_of(m)
            k = key_of(m.get("role"), t)
            if k not in cache:
                cache[k] = count_text(t, MEASURED) + WRAP
            keys.append(k)
            toks.append(cache[k])
        pr.call(keys, toks, count_text(out, MEASURED))
    return {**pr.r, "turns": len(turns)}, pr.P


def references(H: list[dict], is_obs: list[bool]) -> dict[int, list[int]]:
    """obs position -> positions of the assistant messages (replies) that reference it (step 17's unique rule)."""
    count, owner, refs = defaultdict(int), {}, defaultdict(list)
    for p, m in enumerate(H):
        text = text_of(m)
        if m.get("role") == "assistant":
            said = s16.windows(text) | s16.distinctive(text, "tblite")
            for g in said:
                if count.get(g) == 1 and owner.get(g) is not None:
                    refs[owner[g]].append(p)
        for g in s16.windows(text) | s16.distinctive(text, "tblite"):
            count[g] += 1
            owner[g] = p if is_obs[p] else None
    return {j: sorted(set(v)) for j, v in refs.items()}


def variants(d: dict, card, caps: dict) -> dict:
    """The raw arm's trajectory under every variant: {name: (totals, prompt lengths)}."""
    turns = [t for t in d["trajectory"] if t.get("messages")]
    H = list(turns[-1]["messages"]) + [{"role": "assistant", "content": turns[-1].get("response") or ""}]
    L = [len(t["messages"]) for t in turns]
    prefix_ok = all(len(t["messages"]) <= len(H) and all(text_of(a) == text_of(b) and a.get("role") == b.get("role")
                                                         for a, b in zip(t["messages"], H)) for t in turns)
    if not prefix_ok:
        return {"_skipped": True}
    is_obs = [m.get("message_type") == "observation" for m in H]
    obs_pos = [p for p, x in enumerate(is_obs) if x]
    first_obs = obs_pos[0] if obs_pos else -1
    texts = [text_of(m) for m in H]
    full_key = [key_of(m.get("role"), t) for m, t in zip(H, texts)]
    full_tok = [count_text(t, MEASURED) + WRAP for t in texts]
    stub_key = [key_of(m.get("role"), stub_text(t)) if x else None for m, t, x in zip(H, texts, is_obs)]
    stub_tok = [count_text(stub_text(t), MEASURED) + WRAP if x else 0 for t, x in zip(texts, is_obs)]
    G = [count_text(t.get("response") or "", MEASURED) for t in turns]
    refs = references(H, is_obs)
    next_reply = {}
    for j in obs_pos:
        next_reply[j] = next((p for p in range(j + 1, len(H)) if H[p].get("role") == "assistant"), None)

    def run(name):
        pr = Pricer(card)
        stubbed, restored, lost = set(), [], 0                 # restored: (insert after position, obs position)
        for t, n in enumerate(L):
            present = [p for p in obs_pos if p < n]
            newest = present[-1] if present else None
            if name == "raw":
                omit = set()
            elif name.startswith("V1") or name == "masking":
                k = 1 if name == "masking" else int(name.split("k=")[1])
                cut = max(0, (len(present) // k) * k - KEEP)
                omit = set(present[1:cut])
            elif name.startswith("V2"):
                omit = {j for j in present if j != first_obs and j != newest and next_reply[j] not in refs.get(j, [])}
            else:                                              # V3 two-zone
                omit = stubbed
            need = {j for j in present if n in refs.get(j, [])}    # the reply of this call references it
            if name == "V2r":
                for j in need & omit:
                    if j not in {x for _, x in restored}:
                        restored.append((n, j))
            lost += len(need & omit) if name != "V2r" else 0

            def build():
                keys, toks = [], []
                for p in range(n):
                    if p in omit:
                        keys.append(stub_key[p])
                        toks.append(stub_tok[p])
                    else:
                        keys.append(full_key[p])
                        toks.append(full_tok[p])
                    for at, j in restored:
                        if at == p + 1:
                            keys.append(("restored", full_key[j]))
                            toks.append(full_tok[j])
                return keys, toks

            keys, toks = build()
            if name.startswith("V3"):
                cap = caps[name]
                P = sum(toks) + GEN
                if P > cap:
                    for j in sorted((j for j in present if j not in stubbed and j != first_obs and j != newest), reverse=True):
                        if P <= LOW_WATER * cap:
                            break
                        stubbed.add(j)
                        P -= full_tok[j] - stub_tok[j]
                    omit = stubbed
                    keys, toks = build()
                lost += len(need & stubbed)
            pr.call(keys, toks, G[t])
        return {**pr.r, "turns": len(L), "lost": lost, "restores": len(restored)}, pr.P

    names = ["raw", "masking", "V1 k=5", "V1 k=10", "V1 k=20", "V2", "V2r"] + list(caps)
    return {name: run(name) for name in names}


def analyse(job):
    model, arm, instance, raw, caps = job
    card = MODELS[model]["card"]
    try:
        d = json.loads(raw)
    except ValueError:
        return {"arm": arm, "instance": instance, "unreadable": True}, {}
    out = {"arm": arm, "instance": instance, "unreadable": False}
    lengths = {}
    tot, P = as_run(d, card)
    out.update({f"{arm}|{k}": v for k, v in tot.items()})
    lengths[arm] = P
    if arm == "raw" and caps is not None:
        res = variants(d, card, caps)
        if res.get("_skipped"):
            out["prefix_ok"] = False
        else:
            out["prefix_ok"] = True
            for name, (t, Ps) in res.items():
                out.update({f"cf {name}|{k}": v for k, v in t.items()})
                lengths[f"cf {name}"] = Ps
    return out, lengths


def jobs(model, arm, limit, caps):
    m = MODELS[model]
    n = 0
    with tarfile.open(DATA / f"{m['prefix']}{m['arms'][arm]}.tar.gz", "r|gz") as t:
        for x in t:
            if x.isfile() and x.name.endswith(".traj"):
                yield model, arm, x.name.split("/")[-1][:-5], t.extractfile(x).read(), caps
                n += 1
                if limit and n >= limit:
                    break


def pool_run(gen, workers):
    rows, pending = [], set()
    with ProcessPoolExecutor(workers) as pool:
        for job in gen:
            pending.add(pool.submit(analyse, job))
            if len(pending) >= 3 * workers:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                rows += [x.result() for x in done]
        rows += [x.result() for x in pending]
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="coder", choices=list(MODELS))
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    rows, lengths = defaultdict(dict), defaultdict(list)
    for arm in ("masking", "summary"):                         # first: the masking arm's lengths give V3's caps
        for out, ls in pool_run(jobs(a.model, arm, a.limit, None), a.workers):
            rows[out["instance"]].update({k: v for k, v in out.items() if k not in ("arm", "instance", "unreadable")},
                                         instance=out["instance"])
            rows[out["instance"]][f"{arm}|unreadable"] = out["unreadable"]
            for k, v in ls.items():
                lengths[k] += v
    Ps = sorted(lengths["masking"])
    caps = {f"V3 cap=median {Ps[len(Ps) // 2]}": Ps[len(Ps) // 2], f"V3 cap=p90 {Ps[int(0.9 * len(Ps))]}": Ps[int(0.9 * len(Ps))]}
    for out, ls in pool_run(jobs(a.model, "raw", a.limit, caps), a.workers):
        rows[out["instance"]].update({k: v for k, v in out.items() if k not in ("arm", "instance", "unreadable")},
                                     instance=out["instance"])
        rows[out["instance"]]["raw|unreadable"] = out["unreadable"]
        for k, v in ls.items():
            lengths[k] += v
    rows = list(rows.values())
    keys = sorted({k for r in rows for k in r}, key=lambda k: (k != "instance", k))
    suffix = "" if not a.limit else "-test"
    with (OUT / f"{a.model}_instances{suffix}.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    (OUT / f"{a.model}_lengths{suffix}.json").write_text(json.dumps(
        {k: {"calls": len(v), "median": statistics.median(v), "p90": sorted(v)[int(0.9 * len(v))]} for k, v in lengths.items() if v}
        | {"caps": caps}, indent=1))
    print(f"{a.model}: {len(rows)} instances; caps {caps}")


if __name__ == "__main__":
    main()
