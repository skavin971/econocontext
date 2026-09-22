"""Built-in tasks used by the offline test suite.

These are fixtures, not products: they exist so the harness can be exercised
without credentials or spend. Real work arrives as a prompt plus data.
"""

BUGGY = '''def parse_numbers(text):
    """Parse comma-separated numbers; ignore whitespace-only fields."""
    return [int(part) for part in text.split(",")]
'''
LEDGER_README = """# ledger

Utilities for parsing and summarising transaction records.

## Contract

`parsing.parse_amounts(text)`
    Split a comma-separated string into integer amounts. Blank and
    whitespace-only fields are ignored entirely. Signed values are preserved.
    An empty string yields an empty list.

`validation.is_within_limit(amount, limit)`
    True when an amount is permitted. An amount exactly equal to the limit is
    permitted; only amounts strictly greater than the limit are rejected.

`aggregate.mean_amount(amounts)`
    Arithmetic mean of the non-None amounts. None entries are skipped and must
    not contribute to the denominator. Returns 0.0 when nothing remains.

`report.rank(entries)`
    Given (name, amount) pairs, return them ordered by amount, largest first,
    compared numerically. Entries with equal amounts keep their input order.

`report.within_budget(entries, limit)`
    Keep the (name, amount) pairs whose amount is permitted by the limit, in
    input order. An amount equal to the limit is permitted.

Run `python -m pytest tests -q` to exercise the visible checks.
"""

SHAPES = [
    '''def {name}(rows):
    """{doc}"""
    if not rows:
        return []
    kept = []
    for row in rows:
        if row is None:
            continue
        kept.append(row)
    return kept
''',
    '''def {name}(rows, floor=0):
    """{doc}"""
    total = 0
    for row in rows:
        if row is None or row < floor:
            continue
        total += row
    return total
''',
    '''def {name}(rows, key=None):
    """{doc}"""
    grouped = {{}}
    for row in rows:
        bucket = key(row) if key else row
        grouped.setdefault(bucket, []).append(row)
    return grouped
''',
    '''def {name}(text, separator=","):
    """{doc}"""
    parts = [part.strip() for part in str(text).split(separator)]
    return [part for part in parts if part]
''',
    '''def {name}(rows, width=2):
    """{doc}"""
    out = []
    for row in rows:
        if isinstance(row, (int, float)):
            out.append(round(float(row), width))
        else:
            out.append(row)
    return out
''',
]

# Deterministic filler. The modules need genuine bulk for delegation economics to
# apply at all -- a finding cannot be cheaper than a file that is three lines long.
VERBS = ("collect", "normalise", "bucket", "flatten", "trim", "merge", "tally", "sweep")
NOUNS = ("entries", "postings", "batches", "accounts", "ledgers", "periods", "totals")


def bulk(seed, count):
    out = []
    for index in range(count):
        verb = VERBS[(seed + index) % len(VERBS)]
        noun = NOUNS[(seed + index * 3) % len(NOUNS)]
        shape = SHAPES[(seed + index) % len(SHAPES)]
        out.append(
            shape.format(
                name=f"{verb}_{noun}_{index:02d}",
                doc=f"{verb.capitalize()} {noun} for internal bookkeeping.",
            )
        )
    return "\n".join(out)


LEDGER = {
    "README.md": LEDGER_README,
    "ledger/__init__.py": '"""Transaction ledger utilities."""\n',
    "ledger/parsing.py": '''"""Input parsing helpers."""


def parse_amounts(text):
    """Split a comma-separated string into integer amounts."""
    return [int(part) for part in text.split(",")]


'''
    + bulk(0, 26),
    "ledger/validation.py": '''"""Validation helpers."""


def is_within_limit(amount, limit):
    """True when an amount is permitted by the limit."""
    return amount < limit


'''
    + bulk(2, 26),
    "ledger/aggregate.py": '''"""Aggregation helpers."""


def mean_amount(amounts):
    """Arithmetic mean of the non-None amounts."""
    values = [amount for amount in amounts if amount is not None]
    if not values:
        return 0.0
    return sum(values) / len(amounts)


'''
    + bulk(4, 26),
    "ledger/report.py": '''"""Reporting helpers."""

from ledger.validation import is_within_limit


def rank(entries):
    """Order (name, amount) pairs by amount, largest first."""
    return sorted(entries, key=lambda entry: str(entry[1]), reverse=True)


def within_budget(entries, limit):
    """Keep entries whose amount is permitted by the limit."""
    return [entry for entry in entries if is_within_limit(entry[1], limit)]


'''
    + bulk(6, 26),
    "tests/test_smoke.py": """from ledger.aggregate import mean_amount
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
""",
}

# Hidden verification deliberately uses different inputs from the visible tests,
# so special-casing the visible values cannot pass it.
LEDGER_VERIFY = """
import sys
sys.path.insert(0, '.')
from ledger.parsing import parse_amounts
from ledger.validation import is_within_limit
from ledger.aggregate import mean_amount
from ledger.report import rank, within_budget
assert parse_amounts('') == [], 'parse_amounts empty string'
assert parse_amounts(' 7 , , -3 ,') == [7, -3], 'parse_amounts blank and signed'
assert is_within_limit(50, 50) is True, 'limit boundary is inclusive'
assert is_within_limit(51, 50) is False, 'limit rejects above'
assert mean_amount([10, None, 20]) == 15.0, 'mean denominator skips None'
assert mean_amount([None]) == 0.0, 'mean of nothing'
assert rank([('a', 9), ('b', 100)]) == [('b', 100), ('a', 9)], 'rank numeric order'
assert within_budget([('x', 25), ('y', 26)], 25) == [('x', 25)], 'budget keeps exact limit'
print('ledger fixture verified')
"""

DEFAULT_FIXTURE = {"coding": "parser", "research": "corpus"}

GOALS = {
    "parser": (
        "Fix parse_numbers so empty or whitespace-only comma-separated fields are "
        "ignored. Preserve valid integers."
    ),
    "ledger": (
        "The ledger package does not match the contract documented in README.md. "
        "Four separate functions are wrong, one in each of parsing.py, validation.py, "
        "aggregate.py and report.py. Diagnose and fix all four without changing the "
        "documented contract or weakening the tests."
    ),
    "corpus": (
        "What happened to annual fuel expenditure in the town's 2024 electric bus "
        "pilot? Cite the corpus."
    ),
}

CORPUS = {
    "policy.txt": "The town's 2024 pilot replaced diesel buses with electric buses. Annual fuel expenditure fell from 100 units to 60 units.",
    "report.txt": "The 2024 bus pilot's maintenance expenditure was unchanged at 20 units. The fleet size remained ten buses.",
    "context.txt": "The town published transport expenditure annually. Capital purchases are reported separately from operating costs.",
}
