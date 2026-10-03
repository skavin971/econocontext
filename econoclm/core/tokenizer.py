"""One tokenizer for every machine: tiktoken's vocab files bundled with litellm.

CLM counts tokens with tiktoken o200k_base. tiktoken downloads its vocab on first
use, and falls back silently to chars/4 when the download is blocked (as in a
sandbox). litellm ships the same vocab files (tiktoken checks their hash), so
pointing TIKTOKEN_CACHE_DIR there gives the exact tokenizer everywhere, with no
download. Used by bench/tblite/run.py (both arms), analysis/ and the tests.
"""

import os
from pathlib import Path


def tiktoken_cache() -> str | None:
    """litellm's bundled tiktoken cache folder, or None if litellm is missing."""
    try:
        import litellm
    except ImportError:
        return None
    d = Path(litellm.__file__).parent / "litellm_core_utils" / "tokenizers"
    return str(d) if d.is_dir() else None


def use_bundled_tokenizer(env: dict | None = None) -> None:
    """Set TIKTOKEN_CACHE_DIR in `env` (default: this process) unless already set."""
    env = os.environ if env is None else env
    if not env.get("TIKTOKEN_CACHE_DIR") and tiktoken_cache():
        env["TIKTOKEN_CACHE_DIR"] = tiktoken_cache()
