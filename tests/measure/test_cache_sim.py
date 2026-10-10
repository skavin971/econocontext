"""The prefix-cache simulation (measure/cache_sim.py), case by case by hand: 16-token blocks, chained,
full blocks only, matched against any earlier prompt of the run."""

from measure.cache_sim import cached_prefix

A = list(range(1000, 1040))                     # 40 tokens: blocks 1-16, 17-32, then 8 left over


def changed(ids, at):
    out = list(ids)
    out[at] = -1 % 2**31
    return out


def test_the_first_prompt_has_nothing_cached():
    assert cached_prefix([A]) == [0]


def test_append_only_reuses_the_previous_prompt_rounded_down_to_16():
    assert cached_prefix([A, A + list(range(10))]) == [0, 32]


def test_an_edit_inside_block_2_keeps_only_block_1():
    assert cached_prefix([A, changed(A, 20)]) == [0, 16]


def test_an_edit_at_the_start_keeps_nothing():
    assert cached_prefix([A, changed(A, 0)]) == [0, 0]


def test_an_identical_repeat_reuses_its_full_blocks_only():
    assert cached_prefix([A, A]) == [0, 32]     # 16·⌊40/16⌋; CLM's code would give 40


def test_a_prompt_shorter_than_one_block_is_never_cached():
    short = A[:15]
    assert cached_prefix([short, short, short + [7]]) == [0, 0, 0]


def test_the_exact_block_boundary():
    two_blocks = A[:32]
    # extended past the boundary: both blocks reused; repeated exactly: the last block is recomputed
    assert cached_prefix([two_blocks, two_blocks + A[32:40] + list(range(8)), two_blocks]) == [0, 32, 16]


def test_vllms_last_token_rule_recomputes_the_last_block_of_a_fully_cached_prompt():
    four_blocks = list(range(64))
    # an exact repeat on a block boundary, and an exact earlier prefix on a boundary: R = 16·⌊(P−1)/16⌋
    assert cached_prefix([four_blocks, four_blocks, four_blocks[:32]]) == [0, 48, 16]
    # off a boundary the rule changes nothing: the partial last block is never cached anyway
    assert cached_prefix([four_blocks, four_blocks[:40]]) == [0, 32]


def test_two_edits_in_a_row_match_any_earlier_prompt():
    b = changed(A, 35)                          # block 3 differs (only 8 tokens there: not cached at all)
    c = changed(A, 20)                          # block 2 differs from both A and b
    d = A + list(range(30))                     # back to A's prefix: its two blocks are still cached
    assert cached_prefix([A, b, c, d]) == [0, 32, 16, 32]
