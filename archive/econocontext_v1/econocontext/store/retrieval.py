"""Keyword retrieval over stored context: the planner's way to get content back
without an LLM call.

Why it exists: RETRIEVE_FROM_STORE needs to find stored segments that are relevant
to the current turn but no longer (or never) in this agent's window.
What it must never do: rank by anything it cannot explain, or return content from
another run.
"""

import re

from ..types import Segment
from .db import AgentDB, row_to_segment

# PLACEHOLDER: identifier-like tokens only. Later: embeddings, recency and
# version-aware ranking.
# Paths (a/b/c.py), dotted names (os.path.join) and identifiers (parse_numbers).
KEYWORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:[./][A-Za-z0-9_]+)+|[A-Za-z_][A-Za-z0-9_]{3,}")
# Very common words carry no signal for code retrieval.
STOPWORDS = {"self", "none", "true", "false", "return", "import", "from", "with", "this",
             "that", "have", "should", "would", "when", "then", "there", "what", "which"}


def keywords(text: str, limit: int = 12) -> list[str]:
    """Identifier-like tokens from the newest message or pending call, most specific first."""
    found = []
    for token in KEYWORD.findall(text):
        if token.lower() in STOPWORDS or token in found:
            continue
        found.append(token)
    # Paths and dotted names first: they are the most specific keys we have.
    found.sort(key=lambda t: (not ("/" in t or "." in t), -len(t)))
    return found[:limit]


def search(db: AgentDB, run_id: str, query_text: str, agent_id: str | None = None,
           exclude_in_window: bool = True, limit: int = 5) -> list[Segment]:
    """Keyword search over stored context.
    PLACEHOLDER: keyword match only. Later: embeddings, recency, version-aware ranking."""
    # PLACEHOLDER: keyword match only. Later: embeddings, recency, version-aware ranking.
    terms = keywords(query_text)
    if not terms:
        return []
    if db.has_fts:
        # Phrase-quote each term so path characters are not read as FTS query syntax.
        match = " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)
        rows = db.rows("SELECT s.* FROM segments_fts f JOIN segments s ON s.rowid = f.rowid "
                       "WHERE segments_fts MATCH ? AND s.run_id=? ORDER BY rank LIMIT ?",
                       (match, run_id, limit * 4))
    else:
        # Fallback without FTS5: substring match on any term, newest first.
        where = " OR ".join("s.text LIKE ?" for _ in terms)
        rows = db.rows(f"SELECT s.* FROM segments s WHERE s.run_id=? AND ({where}) "
                       "ORDER BY s.created_at DESC LIMIT ?",
                       (run_id, *[f"%{t}%" for t in terms], limit * 4))
    results = []
    in_window = set()
    if exclude_in_window and agent_id is not None:
        in_window = {s.id for s in db.window(agent_id)}
    for r in rows:
        segment = row_to_segment(r)
        if segment.id in in_window or any(s.content_hash == segment.content_hash for s in results):
            continue
        results.append(segment)
        if len(results) == limit:
            break
    return results
