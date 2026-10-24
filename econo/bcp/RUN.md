# Run the BCP pilot

Run this from the repository root. No activation, jq, or second terminal:
Use an absolute script path when running from another directory.

```sh
bash econo/bcp/run.sh
```

This makes paid Purdue and Jev requests. Setup did not make live model calls.
The runner uses the code repository's existing .venv and loads its git-ignored
`.env`: GENAI_API_KEY is the Purdue key; TYPESAFE_API_KEY is the Jev key.
Keys are never displayed. Do not put them in pilot.env.

Check local setup without making API calls:

```sh
bash econo/bcp/run.sh --check
```

The preflight renders the actual chat template before starting any paid work.
Jinja2 is required for that rendering; PyTorch is not needed for this pilot.
If missing, install it in the code repository's virtual environment:
`/Users/ttvaroh/Desktop/Code/research/econocontext/.venv/bin/python -m pip install 'jinja2>=3.1'`.
The first check may download the pinned public tokenizer if it is not cached.

## What it does

1. Load QID 741 and DOCID 48471 from pilot.env, extract the gold document directly
   from the decrypted dataset, and create experiment-local inputs automatically.
2. Record the dataset hash, gold answer, and answer-location evidence span, then
   prepare roughly 400-token chunks. The gold answer is reserved for evaluation.
3. Start its own local gateway, generate up to three topics with configured Qwen,
   and automatically accept them. This is recorded as automatic acceptance,
   not human topic review.
4. Score each chunk's relevance using Jev and build roughly 2,000-token pages.
5. Run ranked pages, original-order pages, and full-document control once each,
   then grade with the repository's historical BCP judge template.
6. Write summaries and readable results, and stop its gateway even on failure.

The obituary supplies the spouse's full name and several identity clues, but
does not independently establish every clue. selection-review.json records this
limitation: this is fixed-document answer extraction, not full BCP retrieval.

## View results

Open `econo/bcp/doc-labeling/pilot-001`:

- `rankings.md`: every chunk ranked under each topic, including scores and text.
- `topics.proposed.json`: generated topics.
- `pages.md`: all ranked pages.
- `pages-used.md`: ranked pages actually supplied to the answering model.
- `answer.md`: ranked-trial answer, status, and page count.
- `summary.csv` and `summary.json`: grading and token comparisons.
- `gateway.log`: gateway startup/errors. Detailed audit traces also stay local.

Rankings appear after scoring. Readable page files appear after the full run;
the underlying pages.ranked.jsonl is created earlier.

## Reruns

Completed stages reuse saved artifacts. Interrupted answering sessions refuse
automatic replay to avoid duplicate calls: inspect logs, then choose a fresh EXP
directory in pilot.env. Changed settings also require a fresh EXP. Keep old runs.
If a stage fails, the runner stops with its error and preserves prior artifacts.

Other candidate IDs are saved in pilot-002.env and pilot-003.env, but still need
document review. This runner uses pilot.env and checks selection-review.json so
its review cannot silently apply to a different question/document.

Citation handling accepts bare chunk IDs and unambiguous document-prefixed labels
only for chunks actually delivered. Invalid actions record their specific reason.
The runner exits unsuccessfully if any trial fails to answer or cannot be graded.
The original citation-failure run is preserved in doc-labeling/pilot-001-failed-citations.
