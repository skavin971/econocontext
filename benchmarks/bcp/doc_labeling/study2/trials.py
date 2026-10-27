"""Load every Study 1 and Study 2 trial and grade it by the string rules (read-only).

Where the trials live (under the BCP data root, default ~/econo/bcp):
- Study 2, our arms (M0-M5): full-search/study2/q<qid>-r<n>/summary.json, one row per arm.
- Study 2, CLM (C1, C2): runs/bcp-s2/<arm>/<qid>-r<n>/ (summary.json; the answer file is
  harbor/*/verifier/answer.md).
- Study 1: full-search/repeats-v1/q<qid>[-labels]-r<n>[b]/summary.json; "r2b" reran the r2 trials that ended in a
  provider error, so it counts as repeat 2.

String grading (the stand-in used until Phase 0):
- contains-gold: the trial answered (not an abstention) and normalize(gold) is a substring of
  normalize(exact_answer). Only the exact_answer field is searched, never the explanation.
- exact: the trial answered and normalize(exact_answer) == normalize(gold).
- An abstention (insufficient_evidence) is incorrect even when its best guess matches; such trials are flagged
  abstained_guess_matches.
- CLM: exact_answer is the "Exact Answer:" line of answer.md (benchmarks/bcp/judge.py:exact_answer); a CLM trial
  without that line has no final answer.
Trials that ended in provider_error are infrastructure failures and are dropped (Study 1 reran them).
"""

import json
from pathlib import Path

from benchmarks.bcp.judge import exact_answer, normalize

BCP = Path.home() / "econo" / "bcp"
ARM_IDS = {"full": "M0", "ranked_doc": "M1", "ranked_doc_search": "M2", "ranked_doc_search_labels": "M3",
           "strict_rank_search_labels": "M4", "strict_rank_search_reasoning": "M5", "clm": "C1", "clm_instructed": "C2",
           "original": "S1-original", "ranked": "S1-ranked", "ranked_doc_labels": "S1-ranked_doc_labels"}
STUDY2_QIDS = ("741", "333", "600", "1226", "1215")
STUDY1_QIDS = ("741", "333", "600")


def rows(path):
    path = Path(path)
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()] if path.exists() else []


def records(bcp=BCP):
    """query_id -> {query, answer, gold_docs, evidence_docs (docid lists)}. The decrypted file is 2.1 GB (it holds
    the documents), so a slim copy is cached once beside it (private, never in git)."""
    bcp = Path(bcp)
    cache = bcp / "cache" / "records-slim.json"
    source = bcp / "BrowseComp-Plus" / "data" / "browsecomp_plus_decrypted.jsonl"
    if cache.exists() and cache.stat().st_mtime >= source.stat().st_mtime:
        return json.loads(cache.read_text())
    out = {}
    with source.open() as f:
        for line in f:
            r = json.loads(line)
            out[str(r["query_id"])] = {"query": r["query"], "answer": r["answer"],
                                       "gold_docs": [str(d["docid"]) for d in r.get("gold_docs", [])],
                                       "evidence_docs": [str(d["docid"]) for d in r.get("evidence_docs", [])]}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out))
    return out


def docids(record, field):
    return set(record.get(field, []))


def grade(gold, answer_text, status):
    """String grades for one trial. status 'answered' is required for credit."""
    answered = status == "answered"
    a, g = normalize(answer_text or ""), normalize(gold)
    hit = bool(a) and bool(g) and g in a
    return {"answered": answered, "exact": answered and bool(a) and a == g, "contains": answered and hit,
            "abstained_guess_matches": status == "insufficient_evidence" and hit}


def answer_text(answer):
    """The response our arms' judge sees (benchmarks/bcp/doc_labeling/run.py:answer_text)."""
    from benchmarks.bcp.doc_labeling.run import answer_text as text
    return text(answer)


def _ours(bcp, study, root, qids, recs):
    for summary in sorted(root.glob("q*-r*/summary.json")):
        folder = summary.parent
        name = folder.name
        qid = name[1:].split("-")[0]
        if qid not in qids:
            continue
        repeat = name.rsplit("-r", 1)[1].rstrip("b")
        for r in json.loads(summary.read_text())["runs"]:
            if r["status"] == "provider_error":
                continue
            a = r.get("answer") or {}
            t = {"study": study, "family": "ours", "qid": qid, "arm": r["arm"], "arm_id": ARM_IDS.get(r["arm"], r["arm"]),
                 "repeat": repeat, "run": name, "status": r["status"], "summary": r,
                 "trial_dir": folder / qid / r["arm"],
                 "calls_log": folder / "gateway" / f"bcp-search.{name}.{qid}.{r['arm']}" / "calls.jsonl",
                 "exact_answer": a.get("exact_answer", ""),
                 "response": answer_text(a) if r["status"] == "answered" and a else None}
            t.update(grade(recs[qid]["answer"], t["exact_answer"], r["status"]))
            yield t


def _clm(bcp, qids, recs):
    for arm in ("clm", "clm_instructed"):
        for out in sorted((bcp / "runs" / "bcp-s2" / arm).glob("*-r*")):
            qid, repeat = out.name.split("-r")
            if qid not in qids or not (out / "summary.json").exists():
                continue
            s = json.loads((out / "summary.json").read_text())
            answer = next(out.glob("harbor/*/verifier/answer.md"), None)
            text = answer.read_text() if answer else ""
            ex = exact_answer(text) or ""
            status = "answered" if ex else (s.get("end") or "no_answer")
            t = {"study": "s2", "family": "clm", "qid": qid, "arm": arm, "arm_id": ARM_IDS[arm], "repeat": repeat,
                 "run": out.name, "status": status, "summary": s, "trial_dir": out,
                 "calls_log": Path(s["calls_log"]) if s.get("calls_log") else None,
                 "exact_answer": ex, "response": text if ex else None}
            t.update(grade(recs[qid]["answer"], ex, status))
            yield t


def load(study="s2", bcp=BCP, qids=None):
    """Every kept trial of one study ('s1' or 's2'), ours and (for s2) CLM, in a stable order."""
    bcp = Path(bcp)
    recs = records(bcp)
    if study == "s2":
        q = set(qids or STUDY2_QIDS)
        return list(_ours(bcp, "s2", bcp / "full-search" / "study2", q, recs)) + list(_clm(bcp, q, recs))
    q = set(qids or STUDY1_QIDS)
    return list(_ours(bcp, "s1", bcp / "full-search" / "repeats-v1", q, recs))
