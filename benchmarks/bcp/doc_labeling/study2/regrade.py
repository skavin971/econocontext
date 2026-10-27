"""Regrade Study 1-2 answers with the official BrowseComp-Plus judge prompt (Phase 0, T2).

What the judge sees, per trial with a final answer (abstentions and no-answer trials stay incorrect, ungraded):
- prompt: prompts.GRADER_TEMPLATE_UPSTREAM (texttron/BrowseComp-Plus scripts_evaluation/evaluate_run.py,
  sha256 in prompts.GRADER_HASHES["upstream"]) filled with the question, the response and the gold answer;
- response: our arms' answer rendered as "Explanation ... Exact Answer: ... Confidence: ...%"
  (doc_labeling/run.py:answer_text); CLM's answer.md, which has the same three fields (only trials whose answer.md
  has an "Exact Answer:" line count as answered);
- sampling: upstream's, temperature 0.7, top_p 0.8, top_k 20, 4,096 output tokens, thinking off, one user message.

Routes (--route):
- vllm: an OpenAI-compatible vLLM server running Qwen/Qwen3-32B (as upstream, which runs vLLM). Thinking is turned
  off with chat_template_kwargs={"enable_thinking": false}, exactly as upstream. This is the official route.
- purdue: Purdue GenAI Studio's "qwen3:32b". It ignores chat_template_kwargs and obeys Ollama's "think": false,
  so it is an Ollama build, whose default qwen3:32b weights are 4-bit (Q4_K_M; not verifiable from the API).
  Labeled provisional: not the official judge.

Verdicts go to <bcp>/judge-phase0/<route>/<study>/<run>.<qid>.<arm>.json (private: they hold judge text), each with
the judge model, route, serving backend, quantization, template hash and sampling. Existing verdicts are kept
(resumable). Parsing follows upstream's parse_judge_response: "correct: yes|no" (bold variants first); a missing
verdict is a parse error, counted as unresolved, never as correct.

Official run on a GPU host (example):
  vllm serve Qwen/Qwen3-32B --port 8000            # bf16, as upstream
  JUDGE_API_KEY=... .venv/bin/python -m benchmarks.bcp.doc_labeling.study2.regrade --route vllm \\
      --base-url http://<host>:8000/v1 --study s2 s1
"""

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from benchmarks.bcp.prompts import GRADER_HASHES, GRADER_TEMPLATE_UPSTREAM

from . import trials as T

SAMPLING = {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "max_tokens": 4096}
ROUTES = {
    "vllm": {"model": "Qwen/Qwen3-32B", "backend": "vLLM (OpenAI-compatible server)", "quantization": "bf16 (as served)",
             "thinking_off": {"chat_template_kwargs": {"enable_thinking": False}}, "official": True,
             "key_env": "JUDGE_API_KEY"},
    "purdue": {"model": "qwen3:32b", "backend": "Purdue GenAI Studio (Open WebUI) -> Ollama",
               "quantization": "unknown; Ollama default qwen3:32b is Q4_K_M (4-bit)",
               "thinking_off": {"think": False}, "official": False, "key_env": "GENAI_API_KEY",
               "base_url": "https://genai.rcac.purdue.edu/api"},
}


def judge_prompt(question, response, correct_answer):
    return GRADER_TEMPLATE_UPSTREAM.format(question=question, response=response, correct_answer=correct_answer)


def parse(text):
    """upstream parse_judge_response, the 'correct' and 'confidence' parts."""
    out = {"correct": None, "confidence": None, "parse_error": False}
    for pattern in (r"\*\*correct:\*\*\s*(yes|no)", r"\*\*correct\*\*:\s*(yes|no)", r"correct:\s*(yes|no)"):
        m = re.search(pattern, text or "", re.IGNORECASE)
        if m:
            out["correct"] = m.group(1).lower() == "yes"
            break
    for pattern in (r"\*\*confidence:\*\*\s*(\d+(?:\.\d+)?)\s*%?", r"\*\*confidence\*\*:\s*(\d+(?:\.\d+)?)\s*%?",
                    r"confidence:\s*(\d+(?:\.\d+)?)\s*%?"):
        m = re.search(pattern, text or "", re.IGNORECASE)
        if m:
            out["confidence"] = min(float(m.group(1)), 100.0)
            break
    out["parse_error"] = out["correct"] is None
    return out


def verdict_path(root, trial):
    return Path(root) / trial["study"] / f"{trial['run']}.{trial['qid']}.{trial['arm']}.json"


class Limiter:
    def __init__(self, rpm):
        self.gap, self.lock, self.next = 60.0 / rpm, threading.Lock(), 0.0

    def wait(self):
        with self.lock:
            now = time.monotonic()
            start = max(now, self.next)
            self.next = start + self.gap
        time.sleep(max(0.0, start - now))


def call(route, base_url, key, prompt, timeout):
    body = {"model": route["model"], "messages": [{"role": "user", "content": prompt}], **SAMPLING,
            **route["thinking_off"]}
    req = urllib.request.Request(base_url.rstrip("/") + "/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {key}"} if key else {})})
    started = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read())
    msg = data["choices"][0]["message"]
    return {"text": msg.get("content") or "", "model": data.get("model"), "usage": data.get("usage"),
            "finish_reason": data["choices"][0].get("finish_reason"), "latency_s": time.monotonic() - started}


def grade_one(trial, recs, route_name, route, base_url, key, out, limiter, timeout, tries):
    path = verdict_path(out, trial)
    if path.exists():
        return json.loads(path.read_text())
    rec = recs[trial["qid"]]
    prompt = judge_prompt(rec["query"], trial["response"], rec["answer"])
    error = None
    for attempt in range(tries):
        limiter.wait()
        try:
            reply = call(route, base_url, key, prompt, timeout)
            break
        except Exception as exc:  # network, HTTP or timeout: retry, then record
            error = f"{type(exc).__name__}: {str(exc)[:200]}"
    else:
        return {"error": error, "trial": [trial["study"], trial["run"], trial["qid"], trial["arm"]]}
    verdict = {"study": trial["study"], "qid": trial["qid"], "arm": trial["arm"], "repeat": trial["repeat"],
               "run": trial["run"], **parse(reply["text"]),
               "judge": {"route": route_name, "official": route["official"], "model": route["model"],
                         "response_model": reply["model"], "backend": route["backend"],
                         "quantization": route["quantization"], "endpoint_host": urllib.parse.urlparse(base_url).netloc,
                         "template": "upstream", "template_sha256": GRADER_HASHES["upstream"],
                         "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                         "sampling": SAMPLING, "thinking_off": route["thinking_off"]},
               "usage": reply["usage"], "finish_reason": reply["finish_reason"], "latency_s": reply["latency_s"],
               "attempts": attempt + 1, "judge_text": reply["text"]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(verdict, indent=1))
    return verdict


def gradable(ts):
    return [t for t in ts if t["answered"] and t["response"]]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--route", choices=list(ROUTES), required=True)
    ap.add_argument("--base-url", help="the judge's OpenAI-compatible base URL (required for vllm)")
    ap.add_argument("--study", nargs="+", default=["s2"], choices=["s1", "s2"])
    ap.add_argument("--bcp", default=str(T.BCP))
    ap.add_argument("--rpm", type=int, default=20)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--tries", type=int, default=3)
    a = ap.parse_args()
    route = ROUTES[a.route]
    base_url = a.base_url or route.get("base_url")
    if not base_url:
        raise SystemExit("--base-url is required for this route")
    key = os.environ.get(route["key_env"])
    if not key and a.route == "purdue":
        from gateway.server import read_env  # the gateway's .env reader (environment first, then .env)
        key = read_env(route["key_env"])
    bcp = Path(a.bcp)
    out = bcp / "judge-phase0" / a.route
    recs = T.records(bcp)
    todo = [t for s in a.study for t in gradable(T.load(s, bcp))]
    limiter = Limiter(a.rpm)
    print(f"{len(todo)} answered trials to grade via {a.route} ({route['model']}); verdicts in {out}", flush=True)
    done = errors = 0
    with concurrent.futures.ThreadPoolExecutor(a.workers) as pool:
        for v in pool.map(lambda t: grade_one(t, recs, a.route, route, base_url, key, out, limiter, a.timeout, a.tries),
                          todo):
            done += 1
            errors += "error" in v
            if done % 10 == 0 or done == len(todo):
                print(f"  {done}/{len(todo)} graded, {errors} errors", flush=True)


if __name__ == "__main__":
    main()
