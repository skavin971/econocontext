"""The single token-counting function every component uses.

Why it exists: token estimates appear in the registry, the cost model, gates and
the assembler; one function keeps them consistent and replaceable in one place.
What it must never do: call a provider (the core is provider-agnostic).
"""

import math

# PLACEHOLDER: characters / 4 is a rough English-text estimate (Anthropic's pricing
# FAQ: "1 token is approximately 4 characters"; Deep Agents uses the same ratio).
# The future version takes the provider's own token counter through the host
# (Gemini supports Count Tokens), and exact reported counts always win in the ledger.
CHARS_PER_TOKEN = 4


def count_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN) if text else 0
