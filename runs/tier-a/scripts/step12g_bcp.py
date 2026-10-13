"""Step 12g: BrowseComp-Plus trajectories (the released run files), offline. No model calls.

Data (outside our repo, never committed; the benchmark asks that its text never appear online in plain text):
~/econo/bcp/BrowseComp-Plus/data/decrypted_run_files/<retriever>/<model>.jsonl (scripts_build_index/
download_run_files.sh) and data/queries_decrypted.jsonl (only the query_id and query columns of
Tevatron/browsecomp-plus). Only numbers are written to runs/tier-a/step12g/.

Per question of each run:
- turns: the model's tool calls plus its final answer (one turn each);
- search calls and document reads (get_document; none in these runs: search returns the top 5 documents,
  each cut to its first 512 tokens);
- tokens by our Qwen tokenizer (measure/tokens.py count_text): search results (the tool output as returned),
  the document text inside them (the snippets), the model's own text (call arguments and the final answer),
  and its reasoning as included (summaries only; the runs do not carry the full reasoning);
- documents returned by more than one search, and the snippet tokens of those repeats (no document is opened).
Labels, one per turn:
- (d) the final answer;
- (b) opening a document that appeared in an earlier result list (get_document; none here);
- (a) a search that follows a name, URL or ID first seen in an earlier search result: the query contains a
  specific name - a run of 2+ capitalized words, a URL, a token mixing letters and digits, or a quoted phrase
  holding one of these or 2+ capitalized words - that appears verbatim (case-sensitive) in an earlier result of
  this question and appears neither in the question nor in the model's earlier queries. (A first pass that
  also took single capitalized words and any quoted phrase, too loose on inspection, is kept as an upper bound:
  runs/tier-a/step12g-4-analysis-loose-names.out.)
- (c) any other search (a new query).
"Tokens added" by a turn: its call arguments and its tool output (a search), or its text (the answer).

Use: PYTHONPATH=. .venv/bin/python runs/tier-a/scripts/step12g_bcp.py [--workers 6]
"""

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from measure.models import MEASURED  # noqa: E402
from measure.tokens import resolve, tokenizer  # noqa: E402

BCP = Path.home() / "econo" / "bcp" / "BrowseComp-Plus" / "data"
OUT = ROOT / "runs" / "tier-a" / "step12g"
RUNS = {  # the released runs; accuracy, recall and search calls from the BrowseComp-Plus paper, Table 1
    "qwen3-embed-8b/gpt5": ("gpt-5", "Qwen3-Embed-8B", 70.12, 78.98, 21.74),
    "qwen3-embed-8b/o3": ("o3", "Qwen3-Embed-8B", 63.49, 73.24, 23.97),
    "bm25/gpt5": ("gpt-5", "BM25", 55.90, 61.70, 23.23),
    "bm25/o3": ("o3", "BM25", 49.28, 56.64, 25.93),
}
STRONGEST = ("qwen3-embed-8b/gpt5", "qwen3-embed-8b/o3", "bm25/gpt5")
BIG = 1000
QUOTED = re.compile(r'"([^"]{3,})"')
CAPS = re.compile(r"\b(?:[A-Z][\w'’.-]*(?:\s+(?:of|de|la|von|van|the|and|&)?\s*[A-Z][\w'’.-]*)*)")
URL = re.compile(r"https?://\S+|www\.\S+")
MIXED = re.compile(r"\b(?=\w*\d)(?=\w*[A-Za-z])\w{3,}\b")


def names(query: str, loose: bool = False) -> set[str]:
    """Specific names in a query: runs of 2+ capitalized words, URLs, tokens mixing letters and digits, and quoted
    phrases holding one of these. loose (the first pass, an upper bound): also single capitalized words of 4+
    characters and any quoted phrase."""
    caps = {m.strip() for m in CAPS.findall(query) if len(m.strip()) >= 4}
    strict = {m for m in caps if len(m.split()) >= 2} | set(URL.findall(query)) | set(MIXED.findall(query))
    out = set(strict)
    for phrase in QUOTED.findall(query):
        if loose or any(s in phrase for s in strict) or len([w for w in phrase.split() if w[:1].isupper()]) >= 2:
            out.add(phrase)
    if loose:
        out |= caps
    return {n for n in out if not re.fullmatch(r"\d{4}", n)}


def counter():
    tok = tokenizer(resolve(MEASURED))
    return lambda texts: [len(x) for x in tok(texts, add_special_tokens=False)["input_ids"]] if texts else []


def analyse(job: tuple[str, list[str], dict]) -> list[dict]:
    run, lines, questions = job
    count = counter()
    rows = []
    for line in lines:
        q = json.loads(line)
        question = questions.get(str(q["query_id"]), "")
        items = q["result"]
        calls = [r for r in items if r["type"] == "tool_call"]
        answers = [r for r in items if r["type"] == "output_text"]
        reasoning = [x.get("text", "") if isinstance(x, dict) else str(x)
                     for r in items if r["type"] == "reasoning" and r.get("output") for x in r["output"]]
        outputs = [r["output"] if isinstance(r["output"], str) else json.dumps(r["output"]) for r in calls]
        args = [r.get("arguments") or "" for r in calls]
        snippets, seen_docs = [], Counter()
        parsed = []
        for o in outputs:
            try:
                res = json.loads(o)
            except ValueError:
                res = []
            parsed.append(res if isinstance(res, list) else [])
            for d in parsed[-1]:
                seen_docs[str(d.get("docid"))] += 1
                snippets.append(d.get("snippet") or "")
        answer = "\n".join(a["output"] if isinstance(a["output"], str) else json.dumps(a["output"]) for a in answers)
        out_tok, arg_tok = count(outputs), count(args)
        snip_tok = count(snippets)
        # repeats: snippet tokens of every occurrence after a document's first
        first, repeat_tok, k = set(), 0, 0
        for res in parsed:
            for d in res:
                if str(d.get("docid")) in first:
                    repeat_tok += snip_tok[k]
                first.add(str(d.get("docid")))
                k += 1
        # labels
        labels, earlier_results, earlier_queries = [], "", ""
        for r, o, a, ot, at in zip(calls, outputs, args, out_tok, arg_tok):
            if r["tool_name"] == "get_document":
                label = "b"
            else:
                try:
                    query = json.loads(a).get("query", a)
                except (ValueError, AttributeError):
                    query = a
                hit = [n for n in names(str(query)) if n in earlier_results and n not in question and n not in earlier_queries]
                label = "a" if hit else "c"
                earlier_queries += "\n" + str(query)
            labels.append((label, ot + at, ot >= BIG))
            earlier_results += "\n" + o
        if answers:
            labels.append(("d", count([answer])[0], False))
        row = {"run": run, "query_id": q["query_id"], "status": q.get("status"), "turns": len(labels),
               "searches": sum(r["tool_name"] == "search" for r in calls),
               "document_reads": sum(r["tool_name"] == "get_document" for r in calls),
               "search_result_tokens": sum(out_tok), "snippet_tokens": sum(snip_tok),
               "own_text_tokens": sum(arg_tok) + (count([answer])[0] if answers else 0),
               "reasoning_tokens_included": sum(count(reasoning)),
               "question_tokens": count([question])[0] if question else 0,
               "docs_returned": len(seen_docs), "docs_returned_twice_or_more": sum(v >= 2 for v in seen_docs.values()),
               "repeat_snippet_tokens": repeat_tok,
               "big_output_tokens": sum(ot for ot in out_tok if ot >= BIG), "big_outputs": sum(ot >= BIG for ot in out_tok)}
        for lab in "abcd":
            row[f"turns_{lab}"] = sum(1 for x in labels if x[0] == lab)
            row[f"tokens_{lab}"] = sum(x[1] for x in labels if x[0] == lab)
        rows.append(row)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    questions = {}
    for line in (BCP / "queries_decrypted.jsonl").read_text().split("\n"):
        if not line:
            continue
        x = json.loads(line)
        questions[str(x["query_id"])] = x["query"]
    jobs = []
    for run in STRONGEST:
        lines = [x for x in (BCP / "decrypted_run_files" / f"{run}.jsonl").read_text().split("\n") if x]
        jobs += [(run, lines[i:i + 40], questions) for i in range(0, len(lines), 40)]
    with ProcessPoolExecutor(a.workers) as pool:
        rows = [r for part in pool.map(analyse, jobs) for r in part]
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "questions.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    summarise(rows)


def summarise(rows: list[dict]) -> None:
    pct = lambda x, z: f"{100 * x / z:.1f}%" if z else "–"   # noqa: E731
    med = lambda v: sorted(v)[len(v) // 2]   # noqa: E731
    out = ["# Step 12g tables (BrowseComp-Plus released runs)", "",
           "## The released runs (accuracy, recall and search calls: BrowseComp-Plus paper, Table 1)", "",
           "| model | retriever | questions | completed | accuracy | recall | search calls (paper) |", "|---|---|---|---|---|---|---|"]
    for run, (model, retriever, acc, rec, calls) in RUNS.items():
        lines = [x for x in (BCP / "decrypted_run_files" / f"{run}.jsonl").read_text().split("\n") if x]
        done = sum(json.loads(x).get("status") == "completed" for x in lines)
        out.append(f"| {model} | {retriever} | {len(lines)} | {done} | {acc:.2f}% | {rec:.2f}% | {calls} |")
    by = defaultdict(list)
    for r in rows:
        by[r["run"]].append(r)
    out += ["", "## Per question (the 3 strongest runs): mean (median)", "",
            "| run | turns | searches | document reads | search-result tokens | of which document snippets | "
            "model's own text | reasoning as included | docs returned ≥2 times | their repeat tokens |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    for run in STRONGEST:
        rs = by[run]
        cell = lambda k: f"{sum(r[k] for r in rs) / len(rs):,.0f} ({med([r[k] for r in rs]):,})"   # noqa: E731
        out.append(f"| {RUNS[run][0]} + {RUNS[run][1]} | {cell('turns')} | {cell('searches')} | {cell('document_reads')} | "
                   f"{cell('search_result_tokens')} | {cell('snippet_tokens')} | {cell('own_text_tokens')} | "
                   f"{cell('reasoning_tokens_included')} | {cell('docs_returned_twice_or_more')} | {cell('repeat_snippet_tokens')} |")
    out += ["", "## Labels: share of turns, and of the tokens the turns add to the context", "",
            "| run | (a) follows a name from a result | (b) opens a listed document | (c) new search | (d) answer |",
            "|---|---|---|---|---|"]
    for run in STRONGEST:
        rs = by[run]
        T = sum(r["turns"] for r in rs)
        K = sum(r[f"tokens_{x}"] for r in rs for x in "abcd")
        out.append(f"| {RUNS[run][0]} + {RUNS[run][1]} | " + " | ".join(
            f"{pct(sum(r[f'turns_{x}'] for r in rs), T)} of turns; {pct(sum(r[f'tokens_{x}'] for r in rs), K)} of tokens"
            for x in "abcd") + " |")
    out += ["", "## ESTIMATE: shares of a trajectory's context tokens (question + model text + tool outputs; "
            "the system prompt and the hidden reasoning are not in the files)", "",
            "| run | (i) big outputs (search results of 1,000+ tokens) | of which document snippets | "
            "repeats of documents already returned | (ii) turns an engine could take over: (a) + (b) |",
            "|---|---|---|---|---|"]
    for run in STRONGEST:
        rs = by[run]
        ctx = sum(r["question_tokens"] + r["own_text_tokens"] + r["search_result_tokens"] for r in rs)
        big = sum(r["big_output_tokens"] for r in rs)
        share_snip = sum(r["snippet_tokens"] for r in rs) / max(1, sum(r["search_result_tokens"] for r in rs))
        out.append(f"| {RUNS[run][0]} + {RUNS[run][1]} | {pct(big, ctx)} | about {pct(big * share_snip, ctx)} | "
                   f"{pct(sum(r['repeat_snippet_tokens'] for r in rs), ctx)} | "
                   f"{pct(sum(r['tokens_a'] + r['tokens_b'] for r in rs), ctx)} of tokens; "
                   f"{pct(sum(r['turns_a'] + r['turns_b'] for r in rs), sum(r['turns'] for r in rs))} of turns |")
    (OUT / "summary.md").write_text("\n".join(out) + "\n")
    print("\n".join(out))


if __name__ == "__main__":
    main()
