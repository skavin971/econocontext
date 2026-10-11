"""What the model sees from our harness, the same for every policy: the system prompt, the tools, the
nudges and the one-tool-call rule. No context-management text: a policy adds none here.

Two interfaces:
- ours (step 11, the default), written by us: small steps, exactly one tool call per reply (extras are
  not run and get a short note), a nudge that says whether the last reply made no tool call or was cut
  off at the output limit, and a cut reply's tool call is not run (its arguments may be incomplete;
  vLLM 0.30 returns a cut call with its cut parameter dropped, step 10b).
- legacy (steps 9 and 10), kept to reproduce old runs: the old system prompt and nudge, every tool
  call of a reply runs, and a cut call runs as vLLM returns it.
The tools are the same in both: bash, read_file, write_file and submit.
"""

from dataclasses import dataclass
from pathlib import Path

TOOLS = [
    {"type": "function", "function": {
        "name": "bash", "description": "Run a bash command in the container; returns its output and exit code.",
        "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
    {"type": "function", "function": {
        "name": "read_file", "description": "Read lines of a text file, with line numbers (default: lines 1-400).",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "write_file", "description": "Write a whole text file (creates directories; replaces the file).",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                       "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "submit", "description": "Finish: call this once the task is fully done.",
        "parameters": {"type": "object", "properties": {}}}},
]


@dataclass(frozen=True)
class Interface:
    name: str
    system: str
    nudge: str              # after a reply with no tool call
    nudge_cut: str          # after a reply cut off at the output limit with no tool call
    one_call: bool          # only a reply's first tool call runs; each other one gets `reject_extra`
    reject_extra: str
    run_cut_calls: bool     # False: a cut reply's tool calls are not run; each gets `cut_call`
    cut_call: str


LEGACY_NUDGE = "Continue by calling a tool. Call `submit` when the task is fully done."
LEGACY = Interface(
    name="legacy",
    system=("You are an autonomous agent completing a task in a Linux container. Work in the current "
            "directory unless the task says otherwise. Use the tools to look around, run commands, and read "
            "and write files. Check your work. When the task is fully done, call `submit`."),
    nudge=LEGACY_NUDGE, nudge_cut=LEGACY_NUDGE, one_call=False, reject_extra="", run_cut_calls=True, cut_call="")

OURS = Interface(
    name="ours",
    system=("You are an agent completing a task in a Linux container. Work in small steps: each reply makes "
            "exactly one tool call, then you see its result and choose the next step. Act rather than plan at "
            "length; keep your reasoning short. Check your work, and call `submit` when the task is done."),
    nudge="Your last reply made no tool call. Reply with your next single action: exactly one tool call.",
    nudge_cut=("Your last reply was cut off at the output limit before it made a tool call. Keep your reasoning "
               "short and reply with your next single action: exactly one tool call."),
    one_call=True,
    reject_extra="Not run: one tool call per reply, and only the first call in your reply was run.",
    run_cut_calls=False,
    cut_call=("Not run: your reply was cut off at the output limit, so this call may be incomplete. Keep your "
              "reasoning short and make your next single action again."))

INTERFACES = {"ours": OURS, "legacy": LEGACY}


def stored_text(path: Path) -> str:
    """A stored text, byte for byte: everything after its header's closing '# ---' line."""
    return Path(path).read_text().split("# ---\n", 1)[1]
