"""PFLOP accuracy, Path B: a 1-minute probe of vLLM's prefix cache before any long run (pflop_accuracy/README.md).

It sends 4 short requests straight to vLLM. Their prompts start with a fresh random line, so nothing is cached from
earlier traffic, and they share a long prefix:
  1. prefix + question A        (cold: nothing cached)
  2. prefix + question B        (shares the prefix with 1)
  3. the same as 2, again       (everything cached except what vLLM recomputes)
  4. 2 + one more turn          (an append-only extension of 2)
For each it prints the prompt length P, vLLM's own cached tokens (usage.prompt_tokens_details.cached_tokens) and the
cached tokens our simulator predicts (measure/cache_sim.py: 16-token blocks, a block chain, the last-token rule) on the
server's own prompt ids. Equal on all 4: the simulator's rules match this server. Different: note the numbers; the
full run (run_clm.py, check_pflops.py) measures the difference call by call.

Use:  .venv/bin/python pflop_accuracy/probe_vllm_cache.py [--vllm http://127.0.0.1:8000/v1]
"""

import argparse
import json
import secrets
import sys
import urllib.request

from common import MODEL, ROOT, VLLM

sys.path.insert(0, str(ROOT))
from measure.cache_sim import cached_prefix  # noqa: E402

FILLER = " ".join(f"Line {i}: the quick brown fox jumps over the lazy dog, and the dog sleeps." for i in range(120))


def ask(url: str, messages: list[dict]) -> dict:
    body = {"model": MODEL, "messages": messages, "max_tokens": 1, "temperature": 0.7, "top_p": 0.95,
            "return_token_ids": True, "chat_template_kwargs": {"enable_thinking": True}}
    request = urllib.request.Request(f"{url.rstrip('/')}/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer EMPTY"})
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.load(response)


def main() -> None:
    ap = argparse.ArgumentParser(description="PFLOP accuracy: probe vLLM's prefix cache")
    ap.add_argument("--vllm", default=VLLM)
    a = ap.parse_args()
    system = {"role": "system", "content": f"Probe {secrets.token_hex(8)}. You are a helpful assistant.\n{FILLER}"}
    two = [system, {"role": "user", "content": "Question B: how many lines are above?"}]
    prompts = [[system, {"role": "user", "content": "Question A: what does the fox do?"}], two, two,
               two + [{"role": "assistant", "content": "There are 120 lines."},
                      {"role": "user", "content": "And how many mention a dog?"}]]
    replies = [ask(a.vllm, m) for m in prompts]
    ids = [r.get("prompt_token_ids") for r in replies]
    if any(i is None for i in ids):
        raise SystemExit("vLLM returned no prompt_token_ids: it must accept return_token_ids (use the vLLM version in the README)")
    real = [((r.get("usage") or {}).get("prompt_tokens_details") or {}).get("cached_tokens") for r in replies]
    if any(x is None for x in real):
        raise SystemExit("vLLM reports no cached tokens: restart it with --enable-prompt-tokens-details "
                         "(and --enable-prefix-caching), as in the README")
    sim = cached_prefix(ids)
    names = ["1 cold", "2 shared prefix", "3 identical to 2", "4 extends 2"]
    print(f"{'request':20} {'P':>6} {'cached (vLLM)':>14} {'cached (ours)':>14}  same?")
    for n, i, r, s in zip(names, ids, real, sim):
        print(f"{n:20} {len(i):6d} {r:14d} {s:14d}  {'yes' if r == s else 'NO'}")
    same = all(r == s for r, s in zip(real, sim))
    print()
    if same:
        print("The simulator matches this server on all 4 requests. Next: .venv/bin/python pflop_accuracy/run_clm.py --backend vllm")
    else:
        gran = {r % 16 == 0 for r in real}
        print("The simulator differs from this server on this probe; these numbers are worth keeping with the full results.")
        print("vLLM's cached counts " + ("are" if gran == {True} else "are NOT all") + " multiples of 16 tokens.")
        print("You can still run the full check: .venv/bin/python pflop_accuracy/run_clm.py --backend vllm")


if __name__ == "__main__":
    main()
