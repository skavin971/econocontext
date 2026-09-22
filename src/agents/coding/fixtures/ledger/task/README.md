# ledger

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
