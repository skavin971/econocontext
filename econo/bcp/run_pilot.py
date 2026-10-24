"""Local one-question runner. Use run.sh; no manual jq or second terminal."""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

sys.path.insert(0, str(Path.cwd()))


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def stage(name, folder, *args):
    print(f"\n>>> {name}", flush=True)
    subprocess.run([sys.executable, "-m", "benchmarks.bcp.doc_labeling", name,
                    "--experiment", str(folder), *map(str, args)], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="check setup without API calls")
    parser.add_argument("--prepare-only", action="store_true", help="prepare chunks without model calls")
    args = parser.parse_args()
    root, folder = Path(os.environ["BCP_ROOT"]), Path(os.environ["EXP"])
    qid, docid = os.environ["QID"], os.environ["DOCID"]
    queries = root / "BrowseComp-Plus/data/browsecomp_plus_decrypted.jsonl"
    if not queries.is_file():
        raise ValueError(f"Download/decrypt the dataset first: {queries}")
    missing = [k for k in ("GENAI_API_KEY", "TYPESAFE_API_KEY") if not os.environ.get(k)]
    if missing and not args.prepare_only:
        raise ValueError("Set these keys in the code repository's .env: " + ", ".join(missing))
    for package in ("transformers", "tokenizers", "numpy", "jinja2"):
        if importlib.util.find_spec(package) is None:
            raise ValueError(f"Missing {package}; run: {sys.executable} -m pip install {package}")

    from benchmarks.bcp.doc_labeling.common import Tokens, digest, load_experiment, read, settings
    from benchmarks.bcp.doc_labeling.prepare import load_record
    from benchmarks.bcp.doc_labeling.run import TOOLS

    question = load_record(queries, "query_id", qid)
    document = next((d for d in question["gold_docs"] if str(d["docid"]) == docid), None)
    if not document or not document.get("text", "").strip():
        raise ValueError(f"No nonempty gold document {docid} for question {qid}")
    cfg = settings(json.loads((root / "settings.json").read_text()))
    # Exercise the actual chat template before any paid calls or session markers.
    Tokens(cfg).prompt([{"role": "user", "content": "Dependency preflight"}], TOOLS)
    review = json.loads((root / "selection-review.json").read_text())
    if (qid, docid) != (review["query_id"], review["docid"]):
        raise ValueError("New selection needs a document review in selection-review.json first")
    if not question.get("answer") or question["answer"] not in document["text"]:
        raise ValueError("Gold answer not found verbatim; inspect the document before proceeding")
    print(f"Question {qid}, document {docid}: {len(document['text']):,} characters\nOutput: {folder}", flush=True)
    print(f"Limitation: {review['limitation']}", flush=True)
    if args.check:
        print("Local inputs, chat-template rendering, and key presence checked. No API calls made.")
        return

    inputs = folder / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    save(inputs / "selection-review.json", review)
    (inputs / "corpus.jsonl").write_text(json.dumps({"id": docid, "contents": document["text"]}) + "\n")
    start = document["text"].index(question["answer"])
    save(inputs / "evaluation.json", {"answer": question["answer"], "single_document_sufficient": True,
         "evidence_spans": [{"start": start, "end": start + len(question["answer"])}],
         "scope": review["limitation"]})
    with queries.open("rb") as stream:
        revision = "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()
    if (folder / "manifest.json").exists():
        _, manifest, fixture = load_experiment(folder)
        if (fixture != {"query_id": qid, "docid": docid, "question": question["query"], "document": document["text"]}
                or manifest["config"] != cfg or manifest["dataset_revision"] != revision
                or manifest["evaluation_hash"] != digest(read(inputs / "evaluation.json"))):
            raise ValueError("Prepared inputs changed; choose a fresh EXP directory")
        print("Reusing validated preparation and frozen scoring inputs.", flush=True)
    else:
        stage("prepare", folder, "--queries", queries, "--query-id", qid,
              "--corpus", inputs / "corpus.jsonl", "--docid", docid, "--dataset-revision", revision,
              "--selection-rationale", review["rationale"], "--evaluation", inputs / "evaluation.json",
              "--config", root / "settings.json", "--legacy-grader")
    if args.prepare_only:
        return

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    with (folder / "gateway.log").open("a") as log:
        gateway = subprocess.Popen([sys.executable, "-m", "gateway.server", "--provider", cfg["provider"],
            "--port", str(port), "--max-tokens", str(cfg["sampling"]["max_tokens"]),
            "--log-dir", str(folder / "gateway"), "--max-calls-per-run", "300"], stdout=log, stderr=log)
        try:
            for _ in range(60):
                if gateway.poll() is not None:
                    raise RuntimeError(f"Gateway exited; see {folder / 'gateway.log'}")
                try:
                    with urlopen(url + "/health", timeout=1):
                        break
                except (URLError, TimeoutError):
                    time.sleep(0.5)
            else:
                raise RuntimeError("Gateway did not become ready within 30 seconds")
            stage("topics", folder, "--generate", "--gateway", url)
            if not (folder / "topics.json").exists():
                stage("topics", folder, "--freeze", folder / "topics.proposed.json", "--rationale",
                      "Automatically accepted for one-command exploratory pilot; no human topic review")
            stage("rank", folder)
            stage("pages", folder)
            stage("run", folder, "--gateway", url)
            stage("report", folder, "--judge", "--gateway", url)
        finally:
            gateway.terminate()
            try:
                gateway.wait(timeout=10)
            except subprocess.TimeoutExpired:
                gateway.kill()
                gateway.wait()

    pages = [json.loads(line) for line in (folder / "pages.ranked.jsonl").read_text().splitlines() if line.strip()]
    result = json.loads((folder / "sessions/ranked.r1/result.json").read_text())
    def readable(items):
        return "\n".join(f"## Page {p['page']} ({p['tokens']} tokens)\n\n{p['text']}\n" for p in items)
    (folder / "pages.md").write_text(readable(pages))
    (folder / "pages-used.md").write_text(readable(pages[:result["pages_used"]]))
    (folder / "answer.md").write_text(f"Status: {result['status']}\nPages used: {result['pages_used']}\n\n{result['response_text']}\n")
    report = read(folder / "summary.json")
    failed = [r["condition"] for r in report["runs"] if r["status"] != "answered" or not r["judge_parsed"]]
    if len(report["runs"]) != report["expected_sessions"] or failed:
        raise RuntimeError(f"Trials incomplete or unsuccessful: {failed}. See summary.json and session traces at {folder}")
    print(f"\nDone. All trials answered and graded. Open {folder}\nRead rankings.md, pages.md, pages-used.md, answer.md, and summary.csv.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        sys.exit(f"Stopped: {exc}")
