"""Step 15: how much of CLM's cost comes from WHERE and WHEN its edits land, its decisions held fixed. Offline, no
model calls, no change to CLM, the harness or EconoContext. FIXED-TRAJECTORY ESTIMATES: plans (b)-(d) change the
order or timing of the tokens the model would have seen; the trajectory (every reply, every edit's content) is the
logged one.

Data: every CLM run (TBLite: tc9 clm-32k and clm-full, tc10s and tc11v clm-32k; BrowseComp-Plus: bcp13p clm, read
from ~/econo/bcp, never written to the repo except as numbers and ids).

The logical context. CLM edits its context as text (render_editable / parse_back in
baselines/clm/upstream/clm/clm_harness/context_utils/context_string.py): after an edit every later turn comes back
re-serialized (tool outputs fold into user text, consecutive same-role turns merge, an assistant turn becomes
"reasoning, content, name args"). So the context is replayed as PIECES, not messages:
  - a tool output, an assistant turn's content, each tool call as "name args" (CLM's own
    rendering), each paragraph of a reply's reasoning, the system prompt (with the tools block: one header piece);
  - any other text of 30+ characters is a piece of its own (the task, nudges, CLM's notices and summaries), by hash;
  - shorter leftover text is glue, attached to the piece before it; each message adds a 5-token wrapper piece.
Formatting follows content: in plans (a') to (d) a retained piece keeps its old wrapper and glue, so CLM's
re-serialization of retained turns is not replayed; (a) - (a') is the cost of that re-serialization alone.
Pieces are identified by their text (tool-call ids repeat across turns in CLM's requests). Each logged request is decomposed into pieces by exact matching (longest first) against all pieces seen before; the
ordered result K_t is the logical context of call t. A piece's tokens are its text counted with the Qwen tokenizer,
so every plan prices the same pieces; plan (a) in this model is checked against the run's real canonical FLOPs.

An edit is a call whose K_t is not K_{t-1} plus pieces appended at the end. Plans (physical order of the pieces):
  (a) as executed: K_t itself;
  (a') in place: new pieces right after their predecessor in K_t, removals in place, formatting kept;
  (b) tail: at an edit, removed pieces are removed in place, new pieces (notices, summaries, reasoning made visible)
      are appended at the end;
  (c) batched: as (b), but removed pieces stay physically present (logically removed) until the next prompt would
      pass the limit, when all pending removals are applied together;
  (d) cost-planned: at each edit, choose in place now (new pieces after their predecessor in K_t), tail, or deferred
      until the limit, by Eq. 9 over the rest of the run with the real future known. Greedy rollout: each option is
      priced with the remaining edits under a base rule; run once per base rule (in place, tail, deferred) and the
      cheapest kept, so (d) is never worse than those rules; then coordinate descent (one edit's option at a time,
      while the whole run gets cheaper). Runs with at most 6 edits are also searched exhaustively (3^n plans); longer
      runs get 20 seeded random restarts of the descent, as a check.
Limit: CLM's strict target (budget - 2,048): 21,512 on BrowseComp-Plus, 29,952 for clm-32k; clm-full has no budget, so
Purdue's window (65,536) minus the request's max_tokens. A plan's prompt is compared with it after scaling the piece
model's count by the call's real/model ratio under (a).

Pricing: Eq. 9 (measure.flops.eq9) per call, with G_t the logged completion tokens, and a vLLM-style prefix cache
over pieces: R_t = the tokens of the longest leading run of pieces seen in an earlier prompt of the run, rounded down
to 16 and capped by the last-token rule (measure/cache_sim.py at piece granularity).

Reasoning made visible: the pieces matched to an earlier reply's reasoning (stripped by our gateway, kept by CLM, and
sent as text after an edit). Its cost is F(a) - F(a with those pieces and their glue removed from every prompt). Plans
(a)-(d) are also replayed on those reasoning-free contexts (F_nors_*), to separate where the reasoning lands from
where CLM's own edits land.

Use: PYTHONPATH=. .venv/bin/python runs/tier-a/scripts/step15_placement.py [--workers 5] [--only SUBSTRING]
Output: runs/tier-a/step15/{runs,edits}.csv and summary.md
"""

import argparse
import csv
import hashlib
import itertools
import random
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import step12b_analysis as b  # noqa: E402
import step12c_analysis as c12  # noqa: E402
import step14b_rereads as r14  # noqa: E402

from measure.cache_sim import cached_prefix  # noqa: E402
from measure.flops import eq9, prompt_ids  # noqa: E402
from measure.models import MEASURED, load  # noqa: E402
from measure.tokens import tokenizer  # noqa: E402

OUT = b.ROOT / "runs" / "tier-a" / "step15"
MIN_PIECE = 30          # characters: shorter leftover text is glue
WRAP = 5                # tokens per message: <|im_start|>role\n ... <|im_end|>\n
GEN = 3                 # the generation prompt
BLOCK = 16
WINDOW = 65_536
LIMITS = {"bcp": 21_512, "clm-32k": 29_952}
EXHAUSTIVE_MAX = 6      # edits: runs with at most this many are also searched over all 3^n plans
OPTIONS = ("inplace", "tail", "defer")
RESTARTS = 20          # random restarts of the descent, for runs with more edits


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


class Pieces:
    """The run's pieces: key -> text and tokens, and the matching inventory (text -> key) for re-serialized text."""

    def __init__(self):
        self.text, self.tokens, self.inventory, self.kind = {}, {}, {}, {}
        self._cover = {}

    def add(self, key, text: str, kind: str, tokens: int | None = None) -> None:
        if key not in self.text:
            self.text[key], self.kind[key] = text, kind
            self.tokens[key] = r14.count(text) if tokens is None else tokens
        if len(text) >= MIN_PIECE and text not in self.inventory:
            self.inventory[text] = key

    def cover(self, text: str) -> list:
        """Ordered keys of a plain text: known pieces (longest first, exact), leftover text as new pieces or glue."""
        if text in self._cover:
            return self._cover[text]
        if text in self.inventory:
            self._cover[text] = [self.inventory[text]]
            return self._cover[text]
        taken, spans = bytearray(len(text)), []
        for piece in sorted((p for p in self.inventory if len(p) <= len(text) and p in text), key=len, reverse=True):
            start = text.find(piece)
            while start >= 0:
                if not any(taken[start:start + len(piece)]):
                    taken[start:start + len(piece)] = b"\x01" * len(piece)
                    spans.append((start, start + len(piece), self.inventory[piece]))
                start = text.find(piece, start + 1)
        spans.sort()
        keys, cursor, previous = [], 0, ("start",)
        for s, e, key in spans + [(len(text), len(text), None)]:
            if s > cursor:
                rest = text[cursor:s]
                if len(rest.strip()) >= MIN_PIECE:
                    new = ("new", sha(rest))
                    self.add(new, rest, "new")
                    keys.append(new)
                    previous = new
                elif rest.strip():
                    glue = ("glue", previous, sha(rest))
                    self.add(glue, rest, "glue")
                    keys.append(glue)
            if key is not None:
                keys.append(key)
                previous = key
            cursor = e
        self._cover[text] = keys
        return keys


def decompose(rows: list[dict], replies: list[dict], tok) -> tuple[Pieces, list[list]]:
    """The logical context K_t of every call, as piece keys (duplicates numbered)."""
    pieces = Pieces()
    first = rows[0]["request"]
    text = c12.full_text(tok, {**first, "messages": first["messages"][:2]})
    header = text[:text.find("<|im_start|>", 1)]                  # the system message with the tools block
    pieces.add(("hdr",), "", "header", tokens=len(tok(header, add_special_tokens=False)["input_ids"]))
    contexts = []
    for t, row in enumerate(rows):
        if t > 0:                                     # the previous reply's reasoning, paragraph by paragraph
            reasoning = replies[t - 1].get("reasoning_content") or replies[t - 1].get("reasoning") or ""
            for i, para in enumerate(p for p in reasoning.split("\n\n") if p.strip()):
                pieces.add(("rs", t - 1, i), para, "reasoning")
        keys = [("hdr",)]
        for m in row["request"]["messages"][1:]:
            content = b.text_of(m.get("content"))
            if m["role"] == "tool":
                key = ("out", sha(content))             # by content: call ids repeat in CLM's requests
                pieces.add(key, content, "output")
                parts = [key]
            elif m["role"] == "assistant" and m.get("tool_calls"):
                parts = list(pieces.cover(content)) if content.strip() else []
                for call in m["tool_calls"]:
                    fn = call.get("function") or {}
                    args = fn.get("arguments")
                    args = args if isinstance(args, str) else json.dumps(args or {})
                    text = f"{fn.get('name', '')} {args}".strip()
                    key = ("cmd", sha(text))
                    pieces.add(key, text, "command")
                    parts.append(key)
            else:
                parts = list(pieces.cover(content)) if content.strip() else []
            if parts:
                wrap = ("wrap", parts[0])
                pieces.add(wrap, "", "wrapper", tokens=WRAP)
                keys += [wrap] + parts
        seen, numbered = {}, []
        for key in keys:                              # the same piece twice in one request: numbered occurrences
            seen[key] = seen.get(key, 0) + 1
            numbered.append(key if seen[key] == 1 else (key, seen[key]))
            if seen[key] > 1:
                pieces.text[numbered[-1]], pieces.tokens[numbered[-1]] = pieces.text[key], pieces.tokens[key]
                pieces.kind[numbered[-1]] = pieces.kind[key]
        contexts.append(numbered)
    return pieces, contexts


class State:
    def __init__(self):
        self.L, self.D, self.seen, self.F = [], set(), set(), 0.0
        self.extra_tokens, self.extra_calls, self.flushes, self.calls = 0, 0, 0, 0

    def copy(self):
        s = State()
        s.L, s.D, s.seen, s.F = list(self.L), set(self.D), set(self.seen), self.F
        s.extra_tokens, s.extra_calls, s.flushes, s.calls = self.extra_tokens, self.extra_calls, self.flushes, self.calls
        return s


class Replay:
    def __init__(self, contexts, tokens, kinds, G, real_P, limit, ct, ca):
        ids = {}
        self.K = [[ids.setdefault(k, len(ids)) for k in K] for K in contexts]
        self.n, self.anchor = [0] * len(ids), [None] * len(ids)
        for k, i in ids.items():
            self.n[i] = tokens[k]
            base, nth = (k[0], k[1]) if isinstance(k[0], tuple) else (k, 1)
            if kinds[k] in ("wrapper", "glue") and isinstance(base[1], tuple):     # formatting of the piece it follows
                self.anchor[i] = ids.get((base[1], nth), ids.get(base[1]))
        self.G, self.limit, self.ct, self.ca = G, limit, ct, ca
        self.events = [t for t in range(1, len(self.K)) if self.K[t][:len(self.K[t - 1])] != self.K[t - 1]]
        self.model_P = [sum(self.n[i] for i in K) + GEN for K in self.K]
        self.scale = [rp / mp if mp else 1.0 for rp, mp in zip(real_P, self.model_P)]

    def P(self, L):
        return sum(self.n[i] for i in L) + GEN

    def price(self, state: State, t: int) -> None:
        h, cum, R, matched, hashes = 0, 0, 0, True, []
        for i in state.L:
            h = hash((h, i))
            cum += self.n[i]
            hashes.append(h)
            if matched and h in state.seen:
                R = cum
            else:
                matched = False
        P = cum + GEN
        R = min(BLOCK * (R // BLOCK), BLOCK * ((P - 1) // BLOCK))
        state.seen.update(hashes)
        state.F += sum(eq9(P, R, self.G[t], self.ct, self.ca))
        pending = sum(self.n[i] for i in state.D)
        state.extra_tokens += pending
        state.extra_calls += pending > 0
        state.calls += 1

    def step(self, state: State, t: int, option: str) -> None:
        K = self.K[t]
        if option == "exec":
            state.L = list(K)
        else:
            Kset = set(K)
            state.D -= Kset
            present = set(state.L)
            # formatting follows content: a retained piece keeps its old wrapper and glue, and CLM's re-serialization
            # of retained turns (merged messages, roles folded to user) is not replayed
            kept = lambda i: self.anchor[i] is not None and self.anchor[i] in Kset   # noqa: E731
            removed = {i for i in state.L if i not in Kset and i not in state.D and not kept(i)}
            added = [i for i in K if i not in present and not (kept(i) and self.anchor[i] in present)]
            if option == "defer":
                state.D |= removed
                state.L = state.L + added
            elif option == "tail" or t not in self._event_set:
                state.L = [i for i in state.L if i not in removed] + added
            else:                                     # in place: each new piece right after its predecessor in K_t
                L = [i for i in state.L if i not in removed]
                inres = set(L)
                skip = set(K) - set(added)
                for idx, i in enumerate(K):
                    if i in skip:
                        continue
                    j = idx - 1
                    while j >= 0 and K[j] not in inres:
                        j -= 1
                    L.insert(L.index(K[j]) + 1 if j >= 0 else 0, i)
                    inres.add(i)
                state.L = L
            if state.D and self.P(state.L) * self.scale[t] > self.limit:
                state.L = [i for i in state.L if i not in state.D]
                state.D = set()
                state.flushes += 1
        self.price(state, t)

    @property
    def _event_set(self):
        if not hasattr(self, "_es"):
            self._es = set(self.events)
        return self._es

    def run(self, choose, start: State | None = None, t0: int = 0) -> State:
        """choose(t) -> option at an edit (non-edits always append at the end)."""
        state = start or State()
        for t in range(t0, len(self.K)):
            self.step(state, t, choose(t) if t in self._event_set else ("exec" if choose(t) == "exec" else "tail"))
        return state

    def rule(self, option: str) -> State:
        return self.run(lambda t: option)

    def planned(self, base: str) -> tuple[State, dict]:
        """Greedy rollout over the edits, the remaining edits priced under `base`."""
        state, choices = State(), {}
        t = 0
        for e in self.events:
            for u in range(t, e):
                self.step(state, u, "tail")
            best = None
            for option in OPTIONS:
                trial = state.copy()
                self.step(trial, e, option)
                end = self.run(lambda u: base, trial, e + 1)
                if best is None or end.F < best[0]:
                    best = (end.F, option)
            choices[e] = best[1]
            self.step(state, e, best[1])
            t = e + 1
        for u in range(t, len(self.K)):
            self.step(state, u, "tail")
        return state, choices

    def descend(self, plan: dict) -> tuple[float, dict]:
        """Coordinate descent from a plan: change one edit's option at a time while the whole run gets cheaper."""
        plan = dict(plan)
        best = self.run(lambda t: plan.get(t, "tail")).F
        for _ in range(5):
            improved = False
            for e in self.events:
                for option in OPTIONS:
                    if option == plan[e]:
                        continue
                    trial = {**plan, e: option}
                    F = self.run(lambda t: trial.get(t, "tail")).F
                    if F < best - 1e-6 * best:
                        best, plan, improved = F, trial, True
            if not improved:
                break
        return best, plan

    def restarts(self, n: int = RESTARTS, seed: int = 15) -> float | None:
        """Runs too long to search exhaustively: coordinate descent from n random plans (seeded); the best found."""
        if len(self.events) <= EXHAUSTIVE_MAX:
            return None
        rng = random.Random(seed)
        return min(self.descend({e: rng.choice(OPTIONS) for e in self.events})[0] for _ in range(n))

    def exhaustive(self) -> float | None:
        if len(self.events) > EXHAUSTIVE_MAX:
            return None
        best = None
        for combo in itertools.product(OPTIONS, repeat=len(self.events)):
            plan = dict(zip(self.events, combo))
            F = self.run(lambda t: plan.get(t, "tail")).F
            best = F if best is None or F < best else best
        return best


def analyse(meta: dict) -> dict:
    tok = tokenizer(MEASURED)
    k = load(MEASURED)
    ct, ca = k["C_token"], k["C_attn"]
    rows = r14.load_rows(Path(meta["calls_log"]) if Path(meta["calls_log"]).is_absolute() else b.ROOT / meta["calls_log"])
    if not rows:
        return {"run": {"run_id": meta["run_id"], "skipped": True}}
    replies = [(r["response"].get("choices") or [{}])[0].get("message") or {} for r in rows]
    G = [r["usage"]["completion_tokens"] for r in rows]
    real_P = [r["usage"]["prompt_tokens"] for r in rows]
    pieces, contexts = decompose(rows, replies, tok)
    if meta["bench"] == "bcp":
        limit = LIMITS["bcp"]
    elif meta["arm"] == "clm-32k":
        limit = LIMITS["clm-32k"]
    else:
        limit = WINDOW - (rows[0]["request"].get("max_tokens") or 2048)
    rp = Replay(contexts, pieces.tokens, pieces.kind, G, real_P, limit, ct, ca)
    prompts = [prompt_ids(tok, r["request"]) for r in rows]           # the real canonical track, for the check
    real_F = sum(sum(eq9(len(p), R, g, ct, ca)) for p, R, g in zip(prompts, cached_prefix(prompts), G))
    rollback_calls = {e for e in rp.events if any("CONTEXT LIMIT HIT" in pieces.text.get(x[0] if isinstance(x[0], tuple) else x, "")
                                                  for x in contexts[e] if x not in set(contexts[e - 1]))}
    a = rp.rule("exec")
    plans = {"a": a, "a_inplace": rp.rule("inplace"), "b": rp.rule("tail"), "c": rp.rule("defer"),
             # attribution: the tail rule at one kind of edit only, in place (formatting kept) at the other
             "b_rollbacks": rp.run(lambda t: "tail" if t in rollback_calls else "inplace"),
             "b_context_edits": rp.run(lambda t: "inplace" if t in rollback_calls else "tail")}
    d_best, d_choices, d_base = None, None, None
    for base in OPTIONS:
        st, ch = rp.planned(base)
        if d_best is None or st.F < d_best.F:
            d_best, d_choices, d_base = st, ch, base
    F_desc, d_choices = rp.descend(d_choices)                         # then improve it one edit at a time
    if F_desc < d_best.F:
        d_best = rp.run(lambda t: d_choices.get(t, "tail"))
    plans["d"] = d_best
    exhaustive, restarted = rp.exhaustive(), rp.restarts()
    # reasoning made visible: the same run under (a) without the reasoning pieces and their glue
    is_rs = lambda key: pieces.kind.get(key) == "reasoning" or (pieces.kind.get(key) == "glue" and key[1][:1] == ("rs",))  # noqa: E731
    base_key = lambda key: key[0] if isinstance(key, tuple) and key and isinstance(key[0], tuple) and len(key) == 2 and isinstance(key[1], int) else key  # noqa: E731
    stripped = [[x for x in K if not is_rs(base_key(x))] for K in contexts]
    rp_rs = Replay(stripped, pieces.tokens, pieces.kind, G, real_P, limit, ct, ca)
    F_no_rs = rp_rs.rule("exec").F
    nors = {"a": F_no_rs, "b": rp_rs.rule("tail").F, "c": rp_rs.rule("defer").F}
    best_nors, plan_nors = None, None
    for base in OPTIONS:
        st, ch = rp_rs.planned(base)
        if best_nors is None or st.F < best_nors:
            best_nors, plan_nors = st.F, ch
    nors["d"] = min(best_nors, rp_rs.descend(plan_nors)[0]) if rp_rs.events else best_nors
    rs_keys = {base_key(x) for K in contexts for x in K if is_rs(base_key(x))}
    rs_tokens = sum(pieces.tokens[x] for x in rs_keys)
    # the edits
    edits = []
    for e in rp.events:
        prev, cur = contexts[e - 1], contexts[e]
        added = [x for x in cur if x not in set(prev)]
        removed = [x for x in prev if x not in set(cur)]
        first = next((i for i, (x, y) in enumerate(zip(prev, cur)) if x != y), min(len(prev), len(cur)))
        content = lambda K: [x for x in K if pieces.kind[x] not in ("wrapper", "glue")]   # noqa: E731
        pc, cc = content(prev), content(cur)
        first_c = next((i for i, (x, y) in enumerate(zip(pc, cc)) if x != y), min(len(pc), len(cc)))
        kind = ("rollback" if any("CONTEXT LIMIT HIT" in pieces.text.get(base_key(x), "") for x in added)
                else "context edit")
        edits.append({"run_id": meta["run_id"], "call": e, "type": kind,
                      "first_change_tokens": sum(pieces.tokens[x] for x in cur[:first]),
                      "first_content_change_tokens": sum(pieces.tokens[x] for x in cur if x in set(cc[:first_c]))
                      + sum(pieces.tokens[x] for x in cur[:first] if pieces.kind[x] in ("wrapper", "glue")),
                      "first_removal_tokens": next((sum(pieces.tokens[y] for y in prev[:i]) for i, x in enumerate(prev)
                                                    if x not in set(cur) and pieces.kind[x] not in ("wrapper", "glue")), ""),
                      "first_insertion_tokens": next((sum(pieces.tokens[y] for y in cur[:i]) for i, x in enumerate(cur)
                                                      if x not in set(prev) and pieces.kind[x] not in ("wrapper", "glue")), ""),
                      "added_tokens": sum(pieces.tokens[x] for x in added),
                      "added_reasoning_tokens": sum(pieces.tokens[x] for x in added if is_rs(base_key(x))),
                      "removed_tokens": sum(pieces.tokens[x] for x in removed),
                      "d_choice": d_choices.get(e)})
    run = {"bench": meta["bench"], "label": meta["label"], "arm": meta["arm"], "run_id": meta["run_id"],
           "calls": len(rows), "edits": len(rp.events),
           "rollbacks": sum(x["type"] == "rollback" for x in edits), "limit": limit,
           "real_P": sum(real_P), "model_P": sum(rp.model_P), "F_real": real_F,
           **{f"F_{name}": st.F for name, st in plans.items()},
           "F_exhaustive": exhaustive if exhaustive is not None else "",
           "F_restarts": restarted if restarted is not None else "", "d_base": d_base,
           **{f"d_{o}": sum(v == o for v in d_choices.values()) for o in OPTIONS},
           "c_extra_tokens": plans["c"].extra_tokens, "c_extra_calls": plans["c"].extra_calls,
           "c_flushes": plans["c"].flushes, "d_extra_tokens": d_best.extra_tokens, "d_extra_calls": d_best.extra_calls,
           "F_no_reasoning": F_no_rs, "reasoning_tokens": rs_tokens,
           **{f"F_nors_{k}": v for k, v in nors.items()}}
    return {"run": run, "edits": edits}


def metas(only: str | None) -> list[dict]:
    return [m for m in r14.metas(only) if m["arm"].startswith("clm")]


def write(name: str, rows: list[dict]) -> None:
    if rows:
        with (OUT / name).open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--only")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(a.workers) as pool:
        results = [r for r in pool.map(analyse, metas(a.only)) if not r["run"].get("skipped")]
    if a.only:
        for r in results:
            print(json.dumps(r["run"]))
        return
    write("runs.csv", [r["run"] for r in results])
    write("edits.csv", [e for r in results for e in r["edits"]])
    print(f"{len(results)} runs analysed")


if __name__ == "__main__":
    main()
