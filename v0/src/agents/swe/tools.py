"""The SWE agent's tools. Read-only ones are shared with children; the rest are root-only."""

from econocontext.agent import STRING, tool

INTEGER = {"type": "integer"}

READ = tool(
    "read",
    "Read a repository file. Optionally pass 1-based start_line and end_line for a range.",
    {"path": STRING, "start_line": INTEGER, "end_line": INTEGER},
    ["path"],
)

SEARCH = tool(
    "search",
    "Find lines containing an exact string, as path:line:text. Optionally limit to a path.",
    {"query": STRING, "path": STRING},
    ["query"],
)

PATCH = tool(
    "apply_patch",
    "Replace one exact, unique span of a file with new text. An empty 'old' creates "
    "a new file with 'new' as its content.",
    {"path": STRING, "old": STRING, "new": STRING},
    ["path", "old", "new"],
)

BASH = tool(
    "bash",
    "Run a bash command in /testbed inside the repository's environment, for example "
    "python or pytest. Long output keeps its beginning and end.",
    {"command": STRING},
    ["command"],
)
