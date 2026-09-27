def parse_numbers(text):
    """Parse comma-separated numbers; ignore whitespace-only fields."""
    return [int(part) for part in text.split(",")]
