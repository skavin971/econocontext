"""Jev planner: ask Jev how likely a tool result is to be needed again later.

STATUS: empty. `p_need_again` below raises NotImplementedError until you build it.
While it is empty, runs made with --jev fall back to the fixed guesses and log why.

------------------------------------------------------------------------------
WHAT THIS IS FOR
------------------------------------------------------------------------------
Every time a tool returns a result (a file read, a grep, a test run), the planner
needs one number: p_need_again, the probability (0 to 1) that the agent will need
this exact content again later in the task.

Without Jev (the default), that number is a fixed guess from
config/econocontext.yaml -> predictor.kind_need_again (0.3 for every tool result,
scaled down for results over 2,000 tokens). See pricing/predictor.py.

With --jev, the planner calls p_need_again() below instead. The number is used in
the cost of the POINTER option: expected re-read cost = p_need_again x full tokens.
Nothing else in the cost formula changes.

Jev (TypeSafe AI) is a "decision model": you send it some text (the "state") and
typed questions; it answers each with a probability instead of generated text.

------------------------------------------------------------------------------
HOW TO BUILD IT (about 30 lines, standard library only, no new dependency)
------------------------------------------------------------------------------
1. Get a TypeSafe API key and put it in the .env file at the repository root:
       TYPESAFE_API_KEY=...
   The run scripts load .env for you. NEVER print, log or commit the key.

2. Send one HTTP request with urllib.request (standard library):
       POST https://api.typesafe.ai/v1/systemone
       headers: Authorization: Bearer <key>,  Content-Type: application/json
       body:
       {
         "model": "jev-latest",
         "state": "TASK:\\n<task_text>\\n\\nTOOL CALL:\\n<tool_call>\\n\\nRESULT:\\n<tool_result_text>",
         "questions": {
           "needed_again": {
             "type": "noul",
             "instructions": "Will the agent need the specific content of this tool
                              result again later in this task (to edit it, quote it,
                              or reason about its exact lines)?"
           }
         }
       }
   ("noul" is Jev's yes/no question type.)

3. The reply looks like this. Return answers.needed_again.noul (a float 0-1):
       {
         "model": "jev-...",
         "answers": {"needed_again": {"type": "noul", "noul": 0.82}},
         "usage": {"input_tokens": 1234, "output_tokens": 0}
       }

4. Keep it safe:
   - Truncate long text first: the state must fit Jev's context (32K tokens per
     AI/ML API's page; TODO: verify on docs.typesafe.ai). About 4 characters is
     1 token, so cutting the result to ~60,000 characters is plenty.
   - Use a short timeout (e.g. 10 s). On any error just raise: the engine catches
     it and falls back to the fixed guess, and the agent keeps running.
   - On HTTP 429 or 529 (rate limit / overloaded) you may retry once after a pause.

5. Test it:
       .venv/bin/python -m pytest tests/test_planner_jev.py -v          (no key needed)
       set -a; . ./.env; set +a
       .venv/bin/python -m pytest -m live tests/test_planner_jev.py -v  (one real Jev call)

6. Run both tracks on the same instance and compare (see tests/test_planner_jev.py
   for the exact commands), then:
       .venv/bin/python scripts/report.py --label <your label>
   Runs with --jev are listed as "econo+jev", runs without as "econo".
   Each tool-result decision in the DB (decisions.candidates) records p_need_again
   and where it came from ("prior", "jev", or "prior (jev failed: ...)").

API reference: https://docs.typesafe.ai/api.md (read 2026-09-27).
Jev's own cost is NOT counted in run cost for now.
------------------------------------------------------------------------------
"""


def p_need_again(tool_result_text: str, tool_call: str, task_text: str) -> float:
    """Probability (0-1) that the agent will need this tool result again. See steps above."""
    # PLACEHOLDER: not built yet. Build it by following steps 1-6 in the module docstring.
    raise NotImplementedError("jev_planner.p_need_again is not built yet: "
                              "see econocontext/planner/jev_planner.py")
