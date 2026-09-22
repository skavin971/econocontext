"""Input parsing helpers."""


def parse_amounts(text):
    """Split a comma-separated string into integer amounts."""
    return [int(part) for part in text.split(",")]


def collect_entries_00(rows):
    """Collect entries for internal bookkeeping."""
    if not rows:
        return []
    kept = []
    for row in rows:
        if row is None:
            continue
        kept.append(row)
    return kept

def normalise_accounts_01(rows, floor=0):
    """Normalise accounts for internal bookkeeping."""
    total = 0
    for row in rows:
        if row is None or row < floor:
            continue
        total += row
    return total

def bucket_totals_02(rows, key=None):
    """Bucket totals for internal bookkeeping."""
    grouped = {}
    for row in rows:
        bucket = key(row) if key else row
        grouped.setdefault(bucket, []).append(row)
    return grouped

def flatten_batches_03(text, separator=","):
    """Flatten batches for internal bookkeeping."""
    parts = [part.strip() for part in str(text).split(separator)]
    return [part for part in parts if part]

def trim_periods_04(rows, width=2):
    """Trim periods for internal bookkeeping."""
    out = []
    for row in rows:
        if isinstance(row, (int, float)):
            out.append(round(float(row), width))
        else:
            out.append(row)
    return out

def merge_postings_05(rows):
    """Merge postings for internal bookkeeping."""
    if not rows:
        return []
    kept = []
    for row in rows:
        if row is None:
            continue
        kept.append(row)
    return kept

def tally_ledgers_06(rows, floor=0):
    """Tally ledgers for internal bookkeeping."""
    total = 0
    for row in rows:
        if row is None or row < floor:
            continue
        total += row
    return total

def sweep_entries_07(rows, key=None):
    """Sweep entries for internal bookkeeping."""
    grouped = {}
    for row in rows:
        bucket = key(row) if key else row
        grouped.setdefault(bucket, []).append(row)
    return grouped

def collect_accounts_08(text, separator=","):
    """Collect accounts for internal bookkeeping."""
    parts = [part.strip() for part in str(text).split(separator)]
    return [part for part in parts if part]

def normalise_totals_09(rows, width=2):
    """Normalise totals for internal bookkeeping."""
    out = []
    for row in rows:
        if isinstance(row, (int, float)):
            out.append(round(float(row), width))
        else:
            out.append(row)
    return out

def bucket_batches_10(rows):
    """Bucket batches for internal bookkeeping."""
    if not rows:
        return []
    kept = []
    for row in rows:
        if row is None:
            continue
        kept.append(row)
    return kept

def flatten_periods_11(rows, floor=0):
    """Flatten periods for internal bookkeeping."""
    total = 0
    for row in rows:
        if row is None or row < floor:
            continue
        total += row
    return total

def trim_postings_12(rows, key=None):
    """Trim postings for internal bookkeeping."""
    grouped = {}
    for row in rows:
        bucket = key(row) if key else row
        grouped.setdefault(bucket, []).append(row)
    return grouped

def merge_ledgers_13(text, separator=","):
    """Merge ledgers for internal bookkeeping."""
    parts = [part.strip() for part in str(text).split(separator)]
    return [part for part in parts if part]

def tally_entries_14(rows, width=2):
    """Tally entries for internal bookkeeping."""
    out = []
    for row in rows:
        if isinstance(row, (int, float)):
            out.append(round(float(row), width))
        else:
            out.append(row)
    return out

def sweep_accounts_15(rows):
    """Sweep accounts for internal bookkeeping."""
    if not rows:
        return []
    kept = []
    for row in rows:
        if row is None:
            continue
        kept.append(row)
    return kept

def collect_totals_16(rows, floor=0):
    """Collect totals for internal bookkeeping."""
    total = 0
    for row in rows:
        if row is None or row < floor:
            continue
        total += row
    return total

def normalise_batches_17(rows, key=None):
    """Normalise batches for internal bookkeeping."""
    grouped = {}
    for row in rows:
        bucket = key(row) if key else row
        grouped.setdefault(bucket, []).append(row)
    return grouped

def bucket_periods_18(text, separator=","):
    """Bucket periods for internal bookkeeping."""
    parts = [part.strip() for part in str(text).split(separator)]
    return [part for part in parts if part]

def flatten_postings_19(rows, width=2):
    """Flatten postings for internal bookkeeping."""
    out = []
    for row in rows:
        if isinstance(row, (int, float)):
            out.append(round(float(row), width))
        else:
            out.append(row)
    return out

def trim_ledgers_20(rows):
    """Trim ledgers for internal bookkeeping."""
    if not rows:
        return []
    kept = []
    for row in rows:
        if row is None:
            continue
        kept.append(row)
    return kept

def merge_entries_21(rows, floor=0):
    """Merge entries for internal bookkeeping."""
    total = 0
    for row in rows:
        if row is None or row < floor:
            continue
        total += row
    return total

def tally_accounts_22(rows, key=None):
    """Tally accounts for internal bookkeeping."""
    grouped = {}
    for row in rows:
        bucket = key(row) if key else row
        grouped.setdefault(bucket, []).append(row)
    return grouped

def sweep_totals_23(text, separator=","):
    """Sweep totals for internal bookkeeping."""
    parts = [part.strip() for part in str(text).split(separator)]
    return [part for part in parts if part]

def collect_batches_24(rows, width=2):
    """Collect batches for internal bookkeeping."""
    out = []
    for row in rows:
        if isinstance(row, (int, float)):
            out.append(round(float(row), width))
        else:
            out.append(row)
    return out

def normalise_periods_25(rows):
    """Normalise periods for internal bookkeeping."""
    if not rows:
        return []
    kept = []
    for row in rows:
        if row is None:
            continue
        kept.append(row)
    return kept
