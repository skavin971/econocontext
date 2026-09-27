# Live study pilot, 2026-09-25

The full write-up is [report.html](report.html). The reasoning behind the study
is [../../research-notebook.html](../../research-notebook.html).

| Folder | What it is |
|---|---|
| `pilot-gemini/` | Gemini 3.6 Flash, both tasks, P0–P3. The two P3 runs here are **not counted**: they ran under a turn limit that counted helper calls against the main agent. |
| `pilot-gemini-p3/` | The two P3 runs again, after that fix. These are the P3 results. |
| `pilot-gptoss-2/` | gpt-oss-120b, P0 only, after the reasoning fix. Failed: tool calls returned as raw text. Study stopped here; gpt-oss skipped for now. |
| `aborted-pilot-gptoss-reasoning-400/` | gpt-oss P0 and P1 before the fix; P1 ended on a 400 caused by sending reasoning back. |
| `aborted-pilot-*-shared-store/` | 15 runs lost at $0 when two studies shared one database. |
| `smoke*/`, `probe/` | Short checks of each model and policy before the pilot. |

Each folder has `ledger.jsonl` (one line per run: outcome, tokens, cost) and a
`trace.json` per run. Every request and response is in `logs/run-<unix>-<run_id>.txt`.

Total from the ledgers: **$4.10**.
