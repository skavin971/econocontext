"""Aggregation helpers."""


def mean_amount(amounts):
    """Arithmetic mean of the non-None amounts."""
    values = [amount for amount in amounts if amount is not None]
    if not values:
        return 0.0
    return sum(values) / len(amounts)


def trim_ledgers_00(rows, width=2):
    """Trim ledgers for internal bookkeeping."""
    out = []
    for row in rows:
        if isinstance(row, (int, float)):
            out.append(round(float(row), width))
        else:
            out.append(row)
    return out

def merge_entries_01(rows):
    """Merge entries for internal bookkeeping."""
    if not rows:
        return []
    kept = []
    for row in rows:
        if row is None:
            continue
        kept.append(row)
    return kept

def tally_accounts_02(rows, floor=0):
    """Tally accounts for internal bookkeeping."""
    total = 0
    for row in rows:
        if row is None or row < floor:
            continue
        total += row
    return total

def sweep_totals_03(rows, key=None):
    """Sweep totals for internal bookkeeping."""
    grouped = {}
    for row in rows:
        bucket = key(row) if key else row
        grouped.setdefault(bucket, []).append(row)
    return grouped

def collect_batches_04(text, separator=","):
    """Collect batches for internal bookkeeping."""
    parts = [part.strip() for part in str(text).split(separator)]
    return [part for part in parts if part]

def normalise_periods_05(rows, width=2):
    """Normalise periods for internal bookkeeping."""
    out = []
    for row in rows:
        if isinstance(row, (int, float)):
            out.append(round(float(row), width))
        else:
            out.append(row)
    return out

def bucket_postings_06(rows):
    """Bucket postings for internal bookkeeping."""
    if not rows:
        return []
    kept = []
    for row in rows:
        if row is None:
            continue
        kept.append(row)
    return kept

def flatten_ledgers_07(rows, floor=0):
    """Flatten ledgers for internal bookkeeping."""
    total = 0
    for row in rows:
        if row is None or row < floor:
            continue
        total += row
    return total

def trim_entries_08(rows, key=None):
    """Trim entries for internal bookkeeping."""
    grouped = {}
    for row in rows:
        bucket = key(row) if key else row
        grouped.setdefault(bucket, []).append(row)
    return grouped

def merge_accounts_09(text, separator=","):
    """Merge accounts for internal bookkeeping."""
    parts = [part.strip() for part in str(text).split(separator)]
    return [part for part in parts if part]

def tally_totals_10(rows, width=2):
    """Tally totals for internal bookkeeping."""
    out = []
    for row in rows:
        if isinstance(row, (int, float)):
            out.append(round(float(row), width))
        else:
            out.append(row)
    return out

def sweep_batches_11(rows):
    """Sweep batches for internal bookkeeping."""
    if not rows:
        return []
    kept = []
    for row in rows:
        if row is None:
            continue
        kept.append(row)
    return kept

def collect_periods_12(rows, floor=0):
    """Collect periods for internal bookkeeping."""
    total = 0
    for row in rows:
        if row is None or row < floor:
            continue
        total += row
    return total

def normalise_postings_13(rows, key=None):
    """Normalise postings for internal bookkeeping."""
    grouped = {}
    for row in rows:
        bucket = key(row) if key else row
        grouped.setdefault(bucket, []).append(row)
    return grouped

def bucket_ledgers_14(text, separator=","):
    """Bucket ledgers for internal bookkeeping."""
    parts = [part.strip() for part in str(text).split(separator)]
    return [part for part in parts if part]

def flatten_entries_15(rows, width=2):
    """Flatten entries for internal bookkeeping."""
    out = []
    for row in rows:
        if isinstance(row, (int, float)):
            out.append(round(float(row), width))
        else:
            out.append(row)
    return out

def trim_accounts_16(rows):
    """Trim accounts for internal bookkeeping."""
    if not rows:
        return []
    kept = []
    for row in rows:
        if row is None:
            continue
        kept.append(row)
    return kept

def merge_totals_17(rows, floor=0):
    """Merge totals for internal bookkeeping."""
    total = 0
    for row in rows:
        if row is None or row < floor:
            continue
        total += row
    return total

def tally_batches_18(rows, key=None):
    """Tally batches for internal bookkeeping."""
    grouped = {}
    for row in rows:
        bucket = key(row) if key else row
        grouped.setdefault(bucket, []).append(row)
    return grouped

def sweep_periods_19(text, separator=","):
    """Sweep periods for internal bookkeeping."""
    parts = [part.strip() for part in str(text).split(separator)]
    return [part for part in parts if part]

def collect_postings_20(rows, width=2):
    """Collect postings for internal bookkeeping."""
    out = []
    for row in rows:
        if isinstance(row, (int, float)):
            out.append(round(float(row), width))
        else:
            out.append(row)
    return out

def normalise_ledgers_21(rows):
    """Normalise ledgers for internal bookkeeping."""
    if not rows:
        return []
    kept = []
    for row in rows:
        if row is None:
            continue
        kept.append(row)
    return kept

def bucket_entries_22(rows, floor=0):
    """Bucket entries for internal bookkeeping."""
    total = 0
    for row in rows:
        if row is None or row < floor:
            continue
        total += row
    return total

def flatten_accounts_23(rows, key=None):
    """Flatten accounts for internal bookkeeping."""
    grouped = {}
    for row in rows:
        bucket = key(row) if key else row
        grouped.setdefault(bucket, []).append(row)
    return grouped

def trim_totals_24(text, separator=","):
    """Trim totals for internal bookkeeping."""
    parts = [part.strip() for part in str(text).split(separator)]
    return [part for part in parts if part]

def merge_batches_25(rows, width=2):
    """Merge batches for internal bookkeeping."""
    out = []
    for row in rows:
        if isinstance(row, (int, float)):
            out.append(round(float(row), width))
        else:
            out.append(row)
    return out
