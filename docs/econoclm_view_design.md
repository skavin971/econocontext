# EconoCLM-View: design

## Why

**CLM** gives the model one context mechanism: a mirror of its conversation, `LIVE_CTX_MAIN.txt`, which it edits as text.

- What it deletes is gone.
- Every edit rewrites the edited turns as plain text, so the request changes from the first rewritten turn on.

**EconoCLM-Tools** added a lossless database of all outputs, `econo get` / `econo search`, and cost facts. On TBLite, the model never used the tools (0 of 30 runs, REPORT.md §5).

**EconoCLM-View** makes the stored record and the context the same object. Every output is kept; the model chooses what goes into its next prompt, and in what order, by editing **VIEW.md**. The view *is* the context.

## The three arms

- **CLM:** CLM exactly as released (`clm_harness`, `18dc111`).
- **EconoCLM-Tools:** CLM, plus the `econo` database tool, the `[econo]` line, the edit quote, stale flags, and a SKILL.md with three advice sentences. The model still edits CLM's conversation file.
- **EconoCLM-View:** CLM's loop with VIEW.md as the only context mechanism. Full autonomy: the model decides content and order. The runtime builds the prompt from the view and reports facts, and nothing else.

## VIEW.md

`/tmp/.live_ctx/VIEW.md` holds one item per line:

| Line | Puts into the next prompt |
|---|---|
| `turn K` | Past turn K exactly as CLM appended it: the assistant message (reasoning, tool call, provider fields such as Gemini's thought signature), its tool result (already cut to head and tail by CLM), and any runtime notices that followed |
| `obs N` | Saved output N, in full, as a user message `[obs N]` |
| `obs N [lines A-B]` | Lines A to B of output N |
| `note NAME: TEXT` | The model's note, as a user message `[note NAME]` |

**Rules:**
- New turns are appended at the end automatically.
- Removing a line removes it from the prompt only; adding it back restores it exactly (the turn store keeps the original messages).
- Blank lines and `#` lines are ignored.
- Lines that are not view lines, or that name nothing, are reported in the receipt and not kept.

## How the prompt is built (`arms/econo_view/view.py`, `env.py`)

The prompt is CLM's protected prefix (system and task) followed by `render(view)`.

**`render` is a pure function of the view, the turn store and the output store:**
- The same view always gives the same bytes.
- It follows the view's order exactly.
- It merges consecutive same-role messages at line boundaries (user+user, text-only assistant+assistant), as CLM's `_normalize` does.
- A turn's assistant tool call and its tool result are never split.
- Every turn ends with a non-assistant message, so the prompt never ends with an assistant turn. Vertex rejects such requests: that was CLM's "rebuild rejected" crash at Gate 4.

**Per step (`ViewContextEnv.step`, a subclass of CLM's `ContextEnv`):**
1. **Sync.** The messages CLM appended since the last step become `turn K` lines. A turn starts at an assistant message and takes every non-assistant message after it. If CLM rewrote the messages itself (rollback on overflow), the view is rebuilt from the surviving turns by fingerprint.
2. Upload VIEW.md, then run the command.
3. **Read VIEW.md back.**
   - If the model changed it: parse it, render it, and pass the result through CLM's own edit gate ("fit": the prompt may grow if it still fits the limit).
   - If accepted: the prompt becomes exactly the rendered view, and a receipt reports tokens before and after, lines dropped and restored, and any ignored lines.
4. **Readout.** CLM's readout (`[context: ~N/M tokens]`), computed on the rendered prompt.

**Equivalence.** With only the automatic appends (the model never edits the view), `render(view)` equals CLM's messages byte for byte. This was checked on all 10 Gate 4 CLM trajectories (`analysis/view_equivalence.py`).

## What is CLM's, unchanged

The agent loop, the budget readout and nudges (computed on the rendered prompt), rollback on overflow with its ledger, the edit gate, the free-turn rule, the finish policy, the output cut, and the Harbor runner.

**Two changes:**
- **System prompt.** Only the "Managing your context" section of CLM's prompt is replaced by TEXT-VIEW (`arms/econo_view/system_section.md`). This is done per instance: the agent wraps its own `_resume.replay`, which CLM's `run` calls once on the opening messages. CLM's module and other agents are not touched.
- **No SKILL.md text.** The arm's skill directory carries only the `econo` tool, so `econo get` / `econo search` work but nothing extra is added to the prompt.

## The [econo] line (`quote/view_status.py`, `quote/costmode.py`)

Facts only, after each command:
- prompt size and number of view lines;
- tokens reused vs recomputed on the last call, and the recent cache-hit rate;
- what a change at the view lines at 25%, 50% and 75% of the prompt would make the next call recompute (an upper bound; equal numbers shown once);
- run cost, number of stored outputs, and stale files.

**Two modes:**

| Mode | Cost unit | Cache rules |
|---|---|---|
| `gemini` (default) | Dollars | The hidden-thinking meter, Gemini's 4,096-token cache minimum (plus a 300-token margin for estimated positions) |
| `qwen` | FLOPs from CLM's `flops_metrics` (`N_body` and attention geometry for model key `27b`, its linear-plus-attention prefill model) | vLLM's 16-token blocks; no hidden-thinking meter |

EconoCLM-Tools keeps its existing line, byte for byte, in `gemini` mode.

## Logs and checks

- **`<run>/view_log.jsonl`:** every step's view, the sha256 of the rendered prompt, whether the model edited the view (as opposed to the automatic appends), lines dropped and restored, and notes.
- **`view_versions/NNNN.md`:** every view the model wrote.
- **`view_turns.json`:** the turn store at the end of the run.
- **`analysis/view_report.py`:**
  - re-renders every logged view and compares sha256 (determinism);
  - counts model edits, drops, restores, notes, obs lines, `econo` use, peak prompt and stale follow-ups, and records where edits happened;
  - checks that each arm carries the expected system prompt.

## Known differences from CLM

- **No format change.** CLM's edit rewrites the turns after the edit as plain text; View keeps each turn's structure. So the "format change" share of the edit ceiling and the rebuild-rejected crash do not occur.
- **New user-turn boundaries.** `obs` and `note` lines are user messages.
  - On Gemini, a newly answered user message resets which earlier thinking is billed.
  - On Qwen, the chat template strips earlier turns' reasoning at user-turn boundaries.
  - Both change the cache and the billed prompt; the analysis reports them.
