# Topic-ranked document pages: one-question pilot

This experiment tests whether a model can answer a BrowseComp-Plus question after
reading fewer parts of one long supporting document. It freezes up to three
question-derived topics, asks Jev for independent chunk/topic scores, then serves
deduplicated pages in topic round-robin order. Original-order pages and a
full-document condition provide controls. See the
[experiment plan](../../../docs/bcp-doc-labeling-plan.md) for the research design.

Run commands from the repository root with Python 3.11+:

```sh
python -m benchmarks.bcp.doc_labeling --help
```

## Prerequisites

- Install the project's `measure` extra for the answering model's tokenizer,
  e.g. `pip install -e '.[dev,measure]'`. This pilot does not require Harbor,
  Docker, AgentDB, or the search service.
- Obtain the benchmark data privately, following BrowseComp-Plus's instructions.
  Have a queries JSONL (`query_id`, `query`) and a full corpus JSONL (`id`,
  `contents` by default). Other document field names can be specified below.
  The loader streams JSONL; it does not use the shared search endpoint's
  truncated 8,192-token view. Parquet/index data must first be exported to a
  private JSONL file using the upstream dataset tooling.
- Choose one pilot/dev query and a long supporting document. Record why that
  document is sufficient. Prepare evidence annotations before looking at Jev's
  scores. Keep the gold answer/evidence separate from topics and model inputs.
- Use a local checkout of the upstream evaluator at a known commit. Preparation
  extracts its `GRADER_TEMPLATE` constant without executing its code. No mutable
  upstream prompt is fetched during grading. The supplied revision identifies
  your checkout; the source and extracted prompt also receive SHA-256 hashes.

All data/artifacts must be outside this repository. The CLI rejects artifact
paths inside the checkout, even under git-ignored `data/`.

For the commands below, set these paths/IDs to your chosen private fixture:

```sh
BCP_ROOT="$HOME/Desktop/Code/research/econo/bcp"
EXPERIMENT="$BCP_ROOT/doc-labeling/pilot-001"
QID=YOUR_QUERY_ID
DOCID=YOUR_DOCUMENT_ID
```

## 1. Prepare and freeze the source

Create a private evaluation JSON, for example
`$BCP_ROOT/doc-labeling-input/evaluation.json`, with this structure:

```json
{
  "answer": "the gold answer from the benchmark",
  "single_document_sufficient": true,
  "evidence_spans": [{"start": 123, "end": 456}]
}
```

Evidence spans are zero-based, half-open Python character offsets into the
original source text. They identify supporting evidence, not just answer-string
occurrences. They stay evaluation-only. Empty spans are allowed when no human
annotation is available, but evidence coverage will then be unknown.

```sh
python -m benchmarks.bcp.doc_labeling prepare \
  --experiment "$EXPERIMENT" \
  --queries "$BCP_ROOT/BrowseComp-Plus/data/queries_decrypted.jsonl" \
  --query-id "$QID" --corpus "$BCP_ROOT/full-corpus.jsonl" --docid "$DOCID" \
  --dataset-revision YOUR_IMMUTABLE_DATASET_REVISION \
  --selection-rationale 'Long supporting document; all required constraints verified' \
  --evaluation "$BCP_ROOT/doc-labeling-input/evaluation.json" \
  --grader-source "$BCP_ROOT/BrowseComp-Plus/scripts_evaluation/evaluate_run.py" \
  --grader-revision YOUR_FULL_40_CHARACTER_UPSTREAM_COMMIT_SHA
```

For a corpus with `docid`/`text`, add `--document-id-field docid
--document-text-field text`. For historical comparison only, `--legacy-grader`
explicitly selects this repository's older grader template instead of the two
upstream-grader arguments. Its report is labeled `repository-legacy`.

Preparation preserves exact source characters and prefers sentence/paragraph
boundaries near 400 answering-model tokens. Long unbroken text is split too.
Every chunk has a stable ID, token count, ordinal, and source interval. The
ordered chunks reconstruct the original text exactly.

Settings can be overridden with `--config /private/path/settings.json` during
preparation, for example:

```json
{"chunk_tokens": 400, "page_tokens": 2000, "max_topics": 3, "repeats": 3}
```

All defaults are in `common.py:DEFAULTS`; the resolved configuration and
tokenizer provenance are frozen in `manifest.json`. Model, provider, context
limit, sampling, call/retry limits, and judge configuration are recorded. The
default model is the repository's measured `qwen3.8:27b`; other models need
matching `measure/` constants/tokenizer support. The default provider is Purdue;
use `"provider": "vllm"` for the repository's stock vLLM tokenization behavior.
The default context limit is the served 65,536-token window, not the model's
larger advertised limit.

## 2. Start the gateway and generate topics

Start the existing gateway with private benchmark logs and the frozen output
cap. It holds the answering/judge model credentials:

```sh
python -m gateway.server --provider purdue \
  --max-tokens-for bcp=4096 --log-dir-for "bcp=$BCP_ROOT/gateway" \
  --max-calls-per-run 300
```

Run the following commands in another shell. Topic generation makes one live
model call; `rank` below makes live Jev calls. Jev reads `TYPESAFE_API_KEY` from
its environment, as the existing predictor does. Set it using your normal
private environment setup; do not put credentials in experiment settings.

```sh
python -m benchmarks.bcp.doc_labeling topics \
  --experiment "$EXPERIMENT" --generate --label bcp-label-pilot001
```

Inspect `topics.proposed.json`. The topic model receives only the question,
never the source document or gold answer. Review/edit the proposal (or make a
reviewed copy), then freeze it:

```sh
python -m benchmarks.bcp.doc_labeling topics \
  --experiment "$EXPERIMENT" --freeze "$EXPERIMENT/topics.proposed.json" \
  --rationale 'Reviewed complementary information needs using the question only'
```

Each topic must contain exactly `id`, `name`, and `description`. IDs are distinct
`t1`, `t2`, or `t3`; there must be one to three topics. A manually authored topic
file is also supported, with the same structure and a recorded rationale.
Topics cannot be refrozen after scoring begins.

## 3. Rank and inspect

```sh
python -m benchmarks.bcp.doc_labeling rank --experiment "$EXPERIMENT"
python -m benchmarks.bcp.doc_labeling pages --experiment "$EXPERIMENT"
```

Jev sees one chunk, the question, and frozen topics per request, and answers one
independent `noul` question per topic. Scores must be finite numbers in [0, 1].
They are relevance scores; calibration is not established. No score is silently
replaced by zero. Only transient failures receive bounded retries. Requests that
would be shortened by Jev's input fitting are rejected before submission.

`rankings.md` shows every topic with full chunk text ranked highest to lowest.
`rankings.json` and `scores.jsonl` are machine-readable. Missing/invalid scores
leave the stage incomplete; rerun `rank` to resume successful work under the
same frozen inputs.

Pages cycle through topics, take each topic's best unused chunk, and continue
the cycle across page boundaries. Duplicate chunk IDs are skipped globally.
All chunks eventually appear; there is no score cutoff. Each serialized page,
including headers, fits the 2,000-token cap. The answerer sees source text and
IDs, not topic scores or gold annotations.

## 4. Run the three conditions

```sh
python -m benchmarks.bcp.doc_labeling run \
  --experiment "$EXPERIMENT" --label bcp-label-pilot001
```

This makes live answering calls. By default it runs three repeats of each of:
`ranked`, `original`, and `full`. The order rotates per repeat and sessions start
fresh. Experiment hashes are included in answering run IDs to separate gateway
logs. All calls use the same frozen model/sampling configuration. Preflight
checks provider, private gateway log routing, and pinned sampling/output limits.

Paged sessions start with one prefetched page. The model can call only
`next_page` or `answer`. Prior pages remain in context. On exhaustion,
`next_page` becomes unavailable and the model gets a final answer/abstention
opportunity. Citations must refer to chunks already delivered. The full
condition gets all chunks in original order in one payload, with the same
source headers, and can only answer.

Only pages included in a submitted model request count as exposed. A page
fetched after the last allowed call does not count as read. A full document
that cannot fit is recorded as `context_limit`, and is unavailable as a savings
baseline. Transport failures retain unknown usage rather than becoming free
calls. Malformed actions, context limits, call limits, exhaustion, abstention,
and errors are explicit outcomes.

Rerunning skips completed sessions. Interrupted sessions are retained and
refused rather than silently restarted into the same logs; use a new experiment
directory for a fresh trial. Frozen source/configuration changes require a new
directory too.

## 5. Grade and report

```sh
# Offline report first (judged correctness is unknown until grading):
python -m benchmarks.bcp.doc_labeling report --experiment "$EXPERIMENT"

# Make live BCP judge calls using the pinned protocol:
python -m benchmarks.bcp.doc_labeling report --experiment "$EXPERIMENT" --judge
```

Results go to `summary.json` and `summary.csv`, with one CSV row per session.
The report includes correctness, exact match, source exposure saved, payload
tokens, provider-reported cumulative input/output, reasoning where reported,
pages, calls, timing, evidence ranks and page positions, and topic/Jev overhead.
The judge's calls live separately. Unparsed verdicts stay unresolved and are
retained for inspection, not retried automatically. Citation entailment remains
an explicit human-review item; evidence visibility alone does not establish it.

Unique source exposure measures how much document text was shown. Actual input
totals include earlier pages and other conversation text sent again on later
calls. These can move in different directions. Savings can be negative; compare
them alongside correctness and evidence. Jev and answerer token counts are not
interchangeable, and the pilot does not infer dollar savings from exposure.
Source exposure counts token positions in the original full-document
tokenization whose character intervals intersect delivered chunks. A token
crossing two chunk boundaries is counted once; omitted passages are never
joined to create artificial tokenizer merges.

Private artifacts include the frozen source/evaluation/protocol, topics,
chunks, scores/attempts, rankings, pages, schedule, per-session calls/messages,
answers, verdicts, and summaries. Do not publish these directories. A shareable
result should contain only reviewed IDs/hashes and aggregate numbers. A single
question supports a case study, not benchmark-wide effectiveness claims.

## Offline verification

With the project's full development environment:

```sh
python -m pytest tests/benchmarks/test_bcp_doc_labeling.py -q
```

The pilot tests themselves need only pytest. In a minimal environment without
Harbor/AgentDB, isolate them from the repository-wide fixtures:

```sh
python -m pytest --confcutdir=tests/benchmarks \
  tests/benchmarks/test_bcp_doc_labeling.py -q
```

All fixtures are synthetic and token counts/providers are injected. Tests make
no model calls or tokenizer downloads. Live tokenizer/model behavior should be
verified in the first real pilot using the gateway's authoritative request logs.
