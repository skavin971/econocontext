"""Reporting helpers."""

from ledger.validation import is_within_limit


def rank(entries):
    """Order (name, amount) pairs by amount, largest first."""
    return sorted(entries, key=lambda entry: str(entry[1]), reverse=True)


def within_budget(entries, limit):
    """Keep entries whose amount is permitted by the limit."""
    return [entry for entry in entries if is_within_limit(entry[1], limit)]


def tally_totals_00(rows, floor=0):
    """Tally totals for internal bookkeeping."""
    total = 0
    for row in rows:
        if row is None or row < floor:
            continue
        total += row
    return total

def sweep_batches_01(rows, key=None):
    """Sweep batches for internal bookkeeping."""
    grouped = {}
    for row in rows:
        bucket = key(row) if key else row
        grouped.setdefault(bucket, []).append(row)
    return grouped

def collect_periods_02(text, separator=","):
    """Collect periods for internal bookkeeping."""
    parts = [part.strip() for part in str(text).split(separator)]
    return [part for part in parts if part]

def normalise_postings_03(rows, width=2):
    """Normalise postings for internal bookkeeping."""
    out = []
    for row in rows:
        if isinstance(row, (int, float)):
            out.append(round(float(row), width))
        else:
            out.append(row)
    return out

def bucket_ledgers_04(rows):
    """Bucket ledgers for internal bookkeeping."""
    if not rows:
        return []
    kept = []
    for row in rows:
        if row is None:
            continue
        kept.append(row)
    return kept

def flatten_entries_05(rows, floor=0):
    """Flatten entries for internal bookkeeping."""
    total = 0
    for row in rows:
        if row is None or row < floor:
            continue
        total += row
    return total

def trim_accounts_06(rows, key=None):
    """Trim accounts for internal bookkeeping."""
    grouped = {}
    for row in rows:
        bucket = key(row) if key else row
        grouped.setdefault(bucket, []).append(row)
    return grouped

def merge_totals_07(text, separator=","):
    """Merge totals for internal bookkeeping."""
    parts = [part.strip() for part in str(text).split(separator)]
    return [part for part in parts if part]

def tally_batches_08(rows, width=2):
    """Tally batches for internal bookkeeping."""
    out = []
    for row in rows:
        if isinstance(row, (int, float)):
            out.append(round(float(row), width))
        else:
            out.append(row)
    return out

def sweep_periods_09(rows):
    """Sweep periods for internal bookkeeping."""
    if not rows:
        return []
    kept = []
    for row in rows:
        if row is None:
            continue
        kept.append(row)
    return kept

def collect_postings_10(rows, floor=0):
    """Collect postings for internal bookkeeping."""
    total = 0
    for row in rows:
        if row is None or row < floor:
            continue
        total += row
    return total

def normalise_ledgers_11(rows, key=None):
    """Normalise ledgers for internal bookkeeping."""
    grouped = {}
    for row in rows:
        bucket = key(row) if key else row
        grouped.setdefault(bucket, []).append(row)
    return grouped

def bucket_entries_12(text, separator=","):
    """Bucket entries for internal bookkeeping."""
    parts = [part.strip() for part in str(text).split(separator)]
    return [part for part in parts if part]

def flatten_accounts_13(rows, width=2):
    """Flatten accounts for internal bookkeeping."""
    out = []
    for row in rows:
        if isinstance(row, (int, float)):
            out.append(round(float(row), width))
        else:
            out.append(row)
    return out

def trim_totals_14(rows):
    """Trim totals for internal bookkeeping."""
    if not rows:
        return []
    kept = []
    for row in rows:
        if row is None:
            continue
        kept.append(row)
    return kept

def merge_batches_15(rows, floor=0):
    """Merge batches for internal bookkeeping."""
    total = 0
    for row in rows:
        if row is None or row < floor:
            continue
        total += row
    return total

def tally_periods_16(rows, key=None):
    """Tally periods for internal bookkeeping."""
    grouped = {}
    for row in rows:
        bucket = key(row) if key else row
        grouped.setdefault(bucket, []).append(row)
    return grouped

def sweep_postings_17(text, separator=","):
    """Sweep postings for internal bookkeeping."""
    parts = [part.strip() for part in str(text).split(separator)]
    return [part for part in parts if part]

def collect_ledgers_18(rows, width=2):
    """Collect ledgers for internal bookkeeping."""
    out = []
    for row in rows:
        if isinstance(row, (int, float)):
            out.append(round(float(row), width))
        else:
            out.append(row)
    return out

def normalise_entries_19(rows):
    """Normalise entries for internal bookkeeping."""
    if not rows:
        return []
    kept = []
    for row in rows:
        if row is None:
            continue
        kept.append(row)
    return kept

def bucket_accounts_20(rows, floor=0):
    """Bucket accounts for internal bookkeeping."""
    total = 0
    for row in rows:
        if row is None or row < floor:
            continue
        total += row
    return total

def flatten_totals_21(rows, key=None):
    """Flatten totals for internal bookkeeping."""
    grouped = {}
    for row in rows:
        bucket = key(row) if key else row
        grouped.setdefault(bucket, []).append(row)
    return grouped

def trim_batches_22(text, separator=","):
    """Trim batches for internal bookkeeping."""
    parts = [part.strip() for part in str(text).split(separator)]
    return [part for part in parts if part]

def merge_periods_23(rows, width=2):
    """Merge periods for internal bookkeeping."""
    out = []
    for row in rows:
        if isinstance(row, (int, float)):
            out.append(round(float(row), width))
        else:
            out.append(row)
    return out

def tally_postings_24(rows):
    """Tally postings for internal bookkeeping."""
    if not rows:
        return []
    kept = []
    for row in rows:
        if row is None:
            continue
        kept.append(row)
    return kept

def sweep_ledgers_25(rows, floor=0):
    """Sweep ledgers for internal bookkeeping."""
    total = 0
    for row in rows:
        if row is None or row < floor:
            continue
        total += row
    return total
