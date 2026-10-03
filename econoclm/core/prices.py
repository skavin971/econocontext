"""The Gemini price vector used for every cost in EconoCLM.

Source: config/billing_rates.yaml on branch feature/claude-code @ ae9fd5a
(vertex_gemini / gemini-3.6-flash, read from the Vertex pricing page 2026-09-27,
global endpoint, promotional period valid until 2026-12-31). Both prompt-length
tiers (<=200K, >200K) are identical, and our budget is 32K anyway.

  input (uncached)   $0.75  per 1M tokens
  cached input       $0.075 per 1M tokens  (90% implicit-cache discount)
  output             $3.75  per 1M tokens  (response AND thinking)
  cache write        no separate charge (Gemini's implicit cache)
"""

from .types import RateRatios

MODEL = "gemini-3.6-flash"

INPUT_PER_MTOK = 0.75
CACHED_PER_MTOK = 0.075
OUTPUT_PER_MTOK = 3.75

# Per single token, in USD (what the quote formulas use).
PRICE_IN = INPUT_PER_MTOK / 1_000_000
PRICE_CACHED = CACHED_PER_MTOK / 1_000_000
PRICE_OUT = OUTPUT_PER_MTOK / 1_000_000

# The same card as ratios to uncached input, for meter.price_call.
RATES = RateRatios(
    model=MODEL,
    output_ratio=OUTPUT_PER_MTOK / INPUT_PER_MTOK,      # 5.0
    cache_read_ratio=CACHED_PER_MTOK / INPUT_PER_MTOK,  # 0.1
    cache_write_ratio=1.0,       # unused: implicit cache has no billed write
    cache_write_1h_ratio=None,
    usd_per_nu=PRICE_IN,
)
