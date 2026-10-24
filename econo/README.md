# Local experiment workspace

This folder now lives inside the repository. Git tracks only the explicitly
allowed runners, configuration examples, instructions, and aggregate reports.
The roughly 7.8 GB of downloaded benchmark data, nested upstream checkout,
indexes, local settings, prompts, and raw traces remain ignored by `econo/.gitignore`.
Do not force-add them: the dataset's canary warns against publishing benchmark text.

From the repository root:

```sh
bash econo/bcp/run.sh --check
bash econo/bcp/run_search.sh --query-ids 741 333 600
```

The second command makes live provider requests. Existing completed trials are
reused; use a fresh `BCP_SEARCH_OUTPUT` for a new experiment. See
[single-document instructions](bcp/RUN.md) and [search instructions](bcp/SEARCH_RUN.md).

Keep `GENAI_API_KEY` (Purdue) and `TYPESAFE_API_KEY` (Jev) in the repository's
ignored `.env`. The runners use `.venv` and load local `pilot.env` if present,
otherwise `pilot.env.example`. Examples contain only paths and selection IDs.

Moving the local workspace does not upload its data. A fresh clone needs the
upstream dataset, tokenizer, dependencies, and locally reviewed selection file;
see [setup](bcp/SETUP.md). No Git LFS dependency is introduced.

The original sibling `econo` path is retained locally as a compatibility symlink
so frozen historical manifests and old commands still resolve. It is not needed
on a fresh clone and is not part of Git.

[Browser comparison](bcp/reports/three-question-token-comparison.html) and
[Markdown comparison](bcp/reports/RESULTS.md) contain aggregate findings only.
