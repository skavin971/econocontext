"""Use tiktoken's vocab bundled with litellm (the download is blocked here), the same
way bench/tblite/run.py does for real runs."""

import os

from econoclm.bench.tblite.run import tiktoken_cache

if not os.environ.get("TIKTOKEN_CACHE_DIR") and tiktoken_cache():
    os.environ["TIKTOKEN_CACHE_DIR"] = tiktoken_cache()
