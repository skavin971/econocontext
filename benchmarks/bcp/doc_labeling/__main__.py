"""Run stages with python -m benchmarks.bcp.doc_labeling --help."""

import argparse
import json
from pathlib import Path

from .common import Gateway, Tokens, load_experiment, read, settings
from .pages import build_pages
from .prepare import freeze_topics, grader_protocol, load_record, prepare, propose_topics
from .rank import score
from .report import build_report
from .run import run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="stage", required=True)
    prep = commands.add_parser("prepare", help="freeze one private question/document pair")
    prep.add_argument("--experiment", required=True)
    prep.add_argument("--queries", required=True, help="private questions JSONL with query_id/query")
    prep.add_argument("--query-id", required=True)
    prep.add_argument("--corpus", required=True, help="full corpus JSONL with id/contents or docid/text")
    prep.add_argument("--docid", required=True)
    prep.add_argument("--document-id-field", default="id")
    prep.add_argument("--document-text-field", default="contents")
    prep.add_argument("--dataset-revision", required=True)
    prep.add_argument("--selection-rationale", required=True)
    prep.add_argument("--evaluation", required=True, help="private JSON with answer, evidence_spans, single_document_sufficient")
    prep.add_argument("--config", help="JSON overrides for frozen experiment settings")
    prep.add_argument("--grader-source", help="local pinned upstream evaluate_run.py; read without executing")
    prep.add_argument("--grader-revision", help="full upstream commit SHA")
    prep.add_argument("--legacy-grader", action="store_true", help="explicitly use the repository's historical template")
    for name in ("topics", "rank", "pages", "run", "report"):
        sub = commands.add_parser(name)
        sub.add_argument("--experiment", required=True)
        if name in ("topics", "run", "report"):
            sub.add_argument("--gateway", default="http://127.0.0.1:8787")
        if name in ("topics", "run"):
            sub.add_argument("--label", default="bcp-label")
        if name == "topics":
            choice = sub.add_mutually_exclusive_group(required=True)
            choice.add_argument("--generate", action="store_true")
            choice.add_argument("--freeze", help="reviewed proposal JSON")
            sub.add_argument("--rationale", default="")
        if name == "report":
            sub.add_argument("--judge", action="store_true", help="make live judge calls for completed answers")
    args = parser.parse_args()
    if args.stage == "prepare":
        cfg = settings(read(args.config) if args.config else None)
        question = load_record(args.queries, "query_id", args.query_id)["query"]
        doc = load_record(args.corpus, args.document_id_field, args.docid)
        evaluation = read(args.evaluation)
        document = doc[args.document_text_field]
        prepare(args.experiment, question, document, query_id=args.query_id, docid=args.docid,
                dataset_revision=args.dataset_revision, selection_rationale=args.selection_rationale,
                evaluation=evaluation, protocol=grader_protocol(args.grader_source, args.grader_revision,
                                                               args.legacy_grader), tokens=Tokens(cfg), cfg=cfg)
    elif args.stage == "topics":
        if args.generate:
            folder, manifest, _ = load_experiment(args.experiment)
            gateway = Gateway(args.gateway, args.label + "." + manifest["fixture_hash"][:12] + ".topics",
                              folder / "topics-generation")
            propose_topics(folder, gateway)
        else:
            freeze_topics(args.experiment, args.freeze, args.rationale)
    elif args.stage == "rank":
        score(args.experiment)
    elif args.stage in ("pages", "run"):
        _, manifest, _ = load_experiment(args.experiment)
        tokens = Tokens(manifest["config"])
        if tokens.provenance != manifest["tokenizer"]:
            parser.error("tokenizer provenance changed")
        if args.stage == "pages":
            build_pages(args.experiment, tokens)
        else:
            run(args.experiment, tokens, args.gateway, args.label)
    else:
        report = build_report(args.experiment, args.gateway if args.judge else None)
        print(json.dumps({"sessions": len(report["runs"]), "expected": report["expected_sessions"]}))
    print(f"{args.stage}: complete (private artifacts at {Path(args.experiment).expanduser()})")


if __name__ == "__main__":
    main()
