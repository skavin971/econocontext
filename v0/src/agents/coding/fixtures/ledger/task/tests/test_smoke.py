from ledger.aggregate import mean_amount
from ledger.parsing import parse_amounts
from ledger.report import rank, within_budget
from ledger.validation import is_within_limit


def test_parse_skips_blank_fields():
    assert parse_amounts("4, ,5,,") == [4, 5]


def test_limit_allows_exact_match():
    assert is_within_limit(10, 10) is True
    assert is_within_limit(11, 10) is False


def test_mean_skips_none_entries():
    assert mean_amount([2, None, 4]) == 3.0


def test_rank_orders_numerically():
    assert rank([("a", 8), ("b", 70)]) == [("b", 70), ("a", 8)]


def test_within_budget_keeps_exact_limit():
    assert within_budget([("a", 10), ("b", 11)], 10) == [("a", 10)]
