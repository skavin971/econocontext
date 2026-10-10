"""Validation helpers."""


def is_within_limit(amount, limit):
    """True when an amount is permitted by the limit."""
    return amount < limit


def bucket_batches_00(rows, key=None):
    """Bucket batches for internal bookkeeping."""
    grouped = {}
    for row in rows:
        bucket = key(row) if key else row
        grouped.setdefault(bucket, []).append(row)
    return grouped

def flatten_periods_01(text, separator=","):
    """Flatten periods for internal bookkeeping."""
    parts = [part.strip() for part in str(text).split(separator)]
    return [part for part in parts if part]

def trim_postings_02(rows, width=2):
    """Trim postings for internal bookkeeping."""
    out = []
    for row in rows:
        if isinstance(row, (int, float)):
            out.append(round(float(row), width))
        else:
            out.append(row)
    return out

def merge_ledgers_03(rows):
    """Merge ledgers for internal bookkeeping."""
    if not rows:
        return []
    kept = []
    for row in rows:
        if row is None:
            continue
        kept.append(row)
    return kept

def tally_entries_04(rows, floor=0):
    """Tally entries for internal bookkeeping."""
    total = 0
    for row in rows:
        if row is None or row < floor:
            continue
        total += row
    return total

def sweep_accounts_05(rows, key=None):
    """Sweep accounts for internal bookkeeping."""
    grouped = {}
    for row in rows:
        bucket = key(row) if key else row
        grouped.setdefault(bucket, []).append(row)
    return grouped

def collect_totals_06(text, separator=","):
    """Collect totals for internal bookkeeping."""
    parts = [part.strip() for part in str(text).split(separator)]
    return [part for part in parts if part]

def normalise_batches_07(rows, width=2):
    """Normalise batches for internal bookkeeping."""
    out = []
    for row in rows:
        if isinstance(row, (int, float)):
            out.append(round(float(row), width))
        else:
            out.append(row)
    return out

def bucket_periods_08(rows):
    """Bucket periods for internal bookkeeping."""
    if not rows:
        return []
    kept = []
    for row in rows:
        if row is None:
            continue
        kept.append(row)
    return kept

def flatten_postings_09(rows, floor=0):
    """Flatten postings for internal bookkeeping."""
    total = 0
    for row in rows:
        if row is None or row < floor:
            continue
        total += row
    return total

def trim_ledgers_10(rows, key=None):
    """Trim ledgers for internal bookkeeping."""
    grouped = {}
    for row in rows:
        bucket = key(row) if key else row
        grouped.setdefault(bucket, []).append(row)
    return grouped

def merge_entries_11(text, separator=","):
    """Merge entries for internal bookkeeping."""
    parts = [part.strip() for part in str(text).split(separator)]
    return [part for part in parts if part]

def tally_accounts_12(rows, width=2):
    """Tally accounts for internal bookkeeping."""
    out = []
    for row in rows:
        if isinstance(row, (int, float)):
            out.append(round(float(row), width))
        else:
            out.append(row)
    return out

def sweep_totals_13(rows):
    """Sweep totals for internal bookkeeping."""
    if not rows:
        return []
    kept = []
    for row in rows:
        if row is None:
            continue
        kept.append(row)
    return kept

def collect_batches_14(rows, floor=0):
    """Collect batches for internal bookkeeping."""
    total = 0
    for row in rows:
        if row is None or row < floor:
            continue
        total += row
    return total

def normalise_periods_15(rows, key=None):
    """Normalise periods for internal bookkeeping."""
    grouped = {}
    for row in rows:
        bucket = key(row) if key else row
        grouped.setdefault(bucket, []).append(row)
    return grouped

def bucket_postings_16(text, separator=","):
    """Bucket postings for internal bookkeeping."""
    parts = [part.strip() for part in str(text).split(separator)]
    return [part for part in parts if part]

def flatten_ledgers_17(rows, width=2):
    """Flatten ledgers for internal bookkeeping."""
    out = []
    for row in rows:
        if isinstance(row, (int, float)):
            out.append(round(float(row), width))
        else:
            out.append(row)
    return out

def trim_entries_18(rows):
    """Trim entries for internal bookkeeping."""
    if not rows:
        return []
    kept = []
    for row in rows:
        if row is None:
            continue
        kept.append(row)
    return kept

def merge_accounts_19(rows, floor=0):
    """Merge accounts for internal bookkeeping."""
    total = 0
    for row in rows:
        if row is None or row < floor:
            continue
        total += row
    return total

def tally_totals_20(rows, key=None):
    """Tally totals for internal bookkeeping."""
    grouped = {}
    for row in rows:
        bucket = key(row) if key else row
        grouped.setdefault(bucket, []).append(row)
    return grouped

def sweep_batches_21(text, separator=","):
    """Sweep batches for internal bookkeeping."""
    parts = [part.strip() for part in str(text).split(separator)]
    return [part for part in parts if part]

def collect_periods_22(rows, width=2):
    """Collect periods for internal bookkeeping."""
    out = []
    for row in rows:
        if isinstance(row, (int, float)):
            out.append(round(float(row), width))
        else:
            out.append(row)
    return out

def normalise_postings_23(rows):
    """Normalise postings for internal bookkeeping."""
    if not rows:
        return []
    kept = []
    for row in rows:
        if row is None:
            continue
        kept.append(row)
    return kept

def bucket_ledgers_24(rows, floor=0):
    """Bucket ledgers for internal bookkeeping."""
    total = 0
    for row in rows:
        if row is None or row < floor:
            continue
        total += row
    return total

def flatten_entries_25(rows, key=None):
    """Flatten entries for internal bookkeeping."""
    grouped = {}
    for row in rows:
        bucket = key(row) if key else row
        grouped.setdefault(bucket, []).append(row)
    return grouped
