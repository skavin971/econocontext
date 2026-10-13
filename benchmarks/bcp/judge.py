"""Grade BrowseComp-Plus answers (step 13a). The CLM paper's judge, Qwen3.5-27B, is not served by Purdue (the model
list fetched through the gateway on 2026-10-13), so the judge is BrowseComp-Plus's own: qwen3:32b with its grading
template (prompts.GRADER_TEMPLATE, verbatim) and its evaluator's sampling (scripts_evaluation/evaluate_run.py:
temperature 0.7, top_p 0.8, top_k 20, up to 4,096 tokens). Judge calls go through our gateway as kind "judge"
under their own run id (<label>.judge), so they are logged but never counted as an agent's calls or FLOPs.
Accuracy here is comparable across our arms, not to the CLM paper's numbers.

Also an exact-match check: the response's "Exact Answer:" line equals the gold answer after normalization
(lower case; punctuation, articles and extra spaces removed).

The response is the agent's answer file (/app/answer.md, kept by the task's verifier). Questions, answers and
responses are read from ~/econo/bcp/ and the judge's verdicts are written there; only numbers reach the repo.

Use: .venv/bin/python benchmarks/bcp/judge.py --label bcp13p
"""

import argparse
import json
import re
import string
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
BCP = Path.home() / "econo" / "bcp"
sys.path.insert(0, str(HERE))
from prompts import GRADER_TEMPLATE  # noqa: E402

JUDGE = {"model": "qwen3:32b", "temperature": 0.7, "top_p": 0.8, "top_k": 20, "max_tokens": 4096}


def load(name: str, field: str) -> dict[str, str]:
    path = BCP / "BrowseComp-Plus" / "data" / name
    return {str(x["query_id"]): x[field] for x in map(json.loads, (l for l in path.read_text().split("\n") if l))}


def normalize(text: str) -> str:
    text = text.lower().strip()
    text = "".join(ch for ch in text if ch not in set(string.punctuation))
    text = re.sub(r"\b(a|an|the)\b", " ", text)
    return " ".join(text.split())


def exact_answer(response: str) -> str | None:
    m = re.search(r"exact answer\s*:\s*(.+)", response, re.IGNORECASE)
    return m.group(1).strip().strip("*").strip() if m else None


def parse(verdict: str) -> bool | None:
    """BrowseComp-Plus's parse_judge_response, the 'correct: yes|no' part."""
    for pattern in (r"\*\*correct:\*\*\s*(yes|no)", r"\*\*correct\*\*:\s*(yes|no)", r"correct:\s*(yes|no)"):
        m = re.search(pattern, verdict, re.IGNORECASE)
        if m:
            return m.group(1).lower() == "yes"
    return None


def judge(gateway: str, run_id: str, question: str, response: str, answer: str) -> tuple[bool | None, str]:
    body = {"model": JUDGE["model"], "temperature": JUDGE["temperature"], "top_p": JUDGE["top_p"],
            "top_k": JUDGE["top_k"], "max_tokens": JUDGE["max_tokens"],
            "messages": [{"role": "user", "content": GRADER_TEMPLATE.format(
                question=question, response=response, correct_answer=answer)}]}
    request = urllib.request.Request(f"{gateway}/run/{run_id}/v1/chat/completions", data=json.dumps(body).encode(),
                                     method="POST", headers={"Content-Type": "application/json",
                                                             "X-Econo-Call-Kind": "judge"})
    with urllib.request.urlopen(request, timeout=900) as r:
        reply = json.load(r)
    text = (reply.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    return parse(text), text


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="bcp13p")
    ap.add_argument("--gateway", default="http://127.0.0.1:8787")
    a = ap.parse_args()
    questions, answers = load("queries_decrypted.jsonl", "query"), load("answers_decrypted.jsonl", "answer")
    verdicts = BCP / "runs" / a.label / "judge.jsonl"
    done = {json.loads(x)["run_id"] for x in verdicts.read_text().split("\n") if x} if verdicts.exists() else set()
    for summary_path in sorted((BCP / "runs" / a.label).glob("*/*/summary.json")):
        s = json.loads(summary_path.read_text())
        if s.get("skipped") or s["run_id"] in done:
            continue
        qid = s["task"]
        answer_file = next(Path(s["trial_dir"]).glob("verifier/answer.md"), None)
        response = answer_file.read_text() if answer_file else ""
        if not response.strip() or response.strip() == "(no answer file)":
            correct, text = False, "(no answer file: counted wrong, not judged)"
        else:
            correct, text = judge(a.gateway, f"{a.label}.judge", questions[qid], response, answers[qid])
        exact = exact_answer(response)
        row = {"run_id": s["run_id"], "arm": s["arm"], "query_id": qid, "judged_correct": correct,
               "judge_parsed": correct is not None, "exact_match": bool(exact) and normalize(exact) == normalize(answers[qid]),
               "has_answer_file": bool(response.strip()) and response.strip() != "(no answer file)", "verdict": text}
        with verdicts.open("a") as f:
            f.write(json.dumps(row) + "\n")
        print(f"{s['run_id']}: judged {correct}, exact {row['exact_match']}", flush=True)


if __name__ == "__main__":
    main()
