"""Copied from econocontext/evidence.py on branch feature/claude-code @ ae9fd5a; the
only change is the tokenizer (CLM's, clm_harness.utils.tokens, instead of
econocontext.tokens). EconoCLM uses the file identity and hashing helpers.

Evidence: what an agent read, identified by the version it read.

Why it exists: "the agent read auth.py twice" means nothing on its own. Read twice
at the same version, the second read was redundant; read before and after an edit,
both were needed. So a piece of evidence is identified by its source AND that
source's version, never by the path alone:

  evidence_id = sha256(kind | source_key | source_version | range)

  file     source_key  repo-relative path
           version     sha256 of the file's bytes when it was read (from the
                       workspace), else of the returned text (then not recoverable:
                       nothing on disk can bring those exact bytes back)
  search   source_key  '<tool>:<args key>' (a grep, glob or listing)
           version     'epoch:<n>', n = workspace changes seen so far in the run;
                       any change may alter a search's result

Events: 'acquired' (a result entered the agent's context at a call) and 'mutated'
(a write to a path, or '*' when which files changed is unknown).
What it must never do: import a host or provider, or treat an unknown tool as a read.
"""

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from clm_harness.utils import tokens as _clm_tokens


def count_tokens(text: str) -> int:
    """CLM's own tokenizer (tiktoken), so our numbers match CLM's readout."""
    return _clm_tokens.count_tokens([{"role": "user", "content": text}])[0]


@dataclass(frozen=True)
class EvidenceRef:
    evidence_id: str
    source_kind: str       # file | search
    source_key: str
    source_version: str
    range: str             # '' for the whole source; otherwise how it was narrowed
    content_hash: str      # sha256 of the text the agent actually received
    byte_size: int
    token_size: int
    recoverable: bool      # whether this exact content can be fetched again from its source


@dataclass(frozen=True)
class EvidenceEvent:
    event: str             # acquired | mutated
    tool_name: str
    args_key: str
    source_key: str
    ref: EvidenceRef | None = None   # acquired only


def sha256(data: bytes | str) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def make_ref(source_kind: str, source_key: str, source_version: str, content: str,
             range: str = "", recoverable: bool = True) -> EvidenceRef:
    evidence_id = sha256(f"{source_kind}|{source_key}|{source_version}|{range}")
    return EvidenceRef(evidence_id, source_kind, source_key, source_version, range,
                       sha256(content), len(content.encode()), count_tokens(content), recoverable)


def normalize_path(path: str, workdir: str | None) -> str:
    """Repo-relative POSIX path when inside the workspace; otherwise normalized absolute."""
    if not workdir:
        return Path(os.path.normpath(path)).as_posix()
    full = os.path.normpath(os.path.join(os.path.realpath(workdir), path))
    relative = os.path.relpath(full, os.path.realpath(workdir))
    return Path(full if relative.startswith("..") else relative).as_posix()


def file_version(workdir: str | None, source_key: str) -> str | None:
    """sha256 of the file's bytes now, or None when it cannot be read."""
    if not workdir:
        return None
    try:
        return "sha256:" + sha256(Path(workdir, source_key).read_bytes())
    except OSError:
        return None
