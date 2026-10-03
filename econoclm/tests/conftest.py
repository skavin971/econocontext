"""Use tiktoken's vocab bundled with litellm, as the runs and the analysis do."""

from econoclm.core.tokenizer import use_bundled_tokenizer

use_bundled_tokenizer()
