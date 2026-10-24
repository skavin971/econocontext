# Local BCP inputs

The dataset is downloaded and decrypted. QID 741 / DOCID 48471 is selected.
See RUN.md for the single run command, keys, outputs, and experiment limitations.

- run.sh loads the code repository's .env and .venv automatically.
- pilot.env contains local paths and selected IDs, not secrets.
- settings.json sets one repeat: ranked, original-order, and full-document trials.
- selection-review.json records the answer-extraction scope and missing clues.
- EXP/inputs is generated automatically; manual root-level full-corpus.jsonl and
  evaluation.json are no longer used by the runner.

This folder is inside the Git repository, but benchmark data and traces stay
Git-ignored. Only explicitly allowed scripts, examples, docs, and aggregate
reports are versioned. See ../README.md.
Keep GENAI_API_KEY (Purdue) and TYPESAFE_API_KEY (Jev) in the code repository's
git-ignored .env. No credentials were created or changed.

## On a fresh clone

The GitHub copy deliberately does not contain benchmark data. From the repository
root, use the project's virtual environment and install the pilot dependencies:

```sh
.venv/bin/python -m pip install transformers tokenizers numpy jinja2 datasets pyarrow huggingface_hub
git clone https://github.com/texttron/BrowseComp-Plus.git econo/bcp/BrowseComp-Plus
git -C econo/bcp/BrowseComp-Plus checkout 046949032b0328319cc9a02663a759ec601d9402
mkdir -p econo/bcp/BrowseComp-Plus/data
.venv/bin/python econo/bcp/BrowseComp-Plus/scripts_build_index/decrypt_dataset.py \
  --output econo/bcp/BrowseComp-Plus/data/browsecomp_plus_decrypted.jsonl
```

For full-corpus search, download all seven Parquet shards of the official
`Tevatron/browsecomp-plus-corpus` dataset at revision
`b27b02bc3e45511b8b82a13e6f90ce761df726f6` into `econo/bcp/corpus-parquet`
(preserving its `data/` subdirectory). The search runner builds the ignored
SQLite index automatically. Allow roughly 8 GB of local disk for this workspace.

Local `pilot.env` is optional; the runner falls back to the versioned example.
The fixed-document runner also requires your own ignored `selection-review.json`
with `query_id`, `docid`, `rationale`, and `limitation`, after reviewing the chosen
document. The complete-corpus search runner does not need that review file.
Neither runner creates credentials or includes them in Git.
