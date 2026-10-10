"""A vLLM-style prefix cache over one run's prompts: how many leading tokens of each were cached.

Why it exists: Purdue's API reports no cached-token count, so R_t (Eq. 9) is simulated from the
prompts' token ids. As vLLM does, a prompt is cut into 16-token blocks and only full blocks are
cached. A block's identity is a hash of the previous block's identity and its own ids (a chain),
so a block can only match at the same position after the same prefix. R_t = 16 × the number of
leading blocks of this prompt already seen in any earlier prompt of the same run. That is the
longest prefix shared with an earlier prompt, rounded down to a multiple of 16. vLLM's last-token
rule caps it: the last token must be computed to get the next one, so a fully cached prompt still
recomputes its last block: R = min(R, 16·⌊(P − 1)/16⌋). Each run starts with an empty cache.
Prompts only, as in CLM: generated tokens are not cached.

(CLM's own code matches a trailing partial block and has no last-token rule, so it gives R = P for
a prompt fully covered by earlier ones. Those are the only differences (docs/tier-a-decisions.md):
- an identical repeat: here P − (P mod 16), or P − 16 when P is a multiple of 16;
- an exact earlier prefix ending on a block boundary: here P − 16.)
"""

import hashlib
from array import array

BLOCK = 16


def block_hashes(ids: list[int], block: int = BLOCK) -> list[bytes]:
    hashes, previous = [], b""
    for start in range(0, len(ids) - len(ids) % block, block):
        previous = hashlib.blake2b(previous + array("I", ids[start:start + block]).tobytes(), digest_size=16).digest()
        hashes.append(previous)
    return hashes


def cached_prefix(prompts: list[list[int]], block: int = BLOCK) -> list[int]:
    """R_t for each prompt of one run, in order."""
    seen, out = set(), []
    for ids in prompts:
        hashes = block_hashes(ids, block)
        matched = 0
        while matched < len(hashes) and hashes[matched] in seen:
            matched += 1
        out.append(min(matched * block, block * ((len(ids) - 1) // block)) if ids else 0)
        seen.update(hashes)
    return out
