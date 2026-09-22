"""Tools only the coding agent has: the ones that change or run something.

Read and search come from BaseAgent. These are root-only, because a child that
can edit files can undo the parent's work without the parent knowing.
"""

from econocontext.agent import STRING, STRINGS, tool

PATCH = tool(
    "apply_patch",
    "Replace an exact unique source span; root only.",
    {"path": STRING, "old": STRING, "new": STRING},
    ["path", "old", "new"],
)

COMMAND = tool(
    "command",
    "Run a bounded argv command in the trusted task workspace.",
    {"argv": STRINGS},
    ["argv"],
)

ALLOWED_COMMANDS = ("python", "python3", "pytest")


def test_tool(description):
    return tool("test", description, {})
