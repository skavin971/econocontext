"""Price a candidate plan as the four cost terms, in normalized units (NU).

Why it exists: the optimizer compares plans by predicted cost; this module is the
one place that turns a plan's sizes into that prediction.
What it must never do: see provider names or change what a candidate is.

PLACEHOLDER: token-length pricing only. Every input token counts as uncached
(ratio 1.0) and nothing is discounted for caching:
  prepare       = tokens sent now + tokens re-read later x p_need_again
                  + (extra model calls x their input tokens)
  work          = expected output tokens x output_ratio, per model call the plan makes
  integrate     = tokens the result adds to the receiving window
  leaves_behind = (tokens left resident + p_need_again x tokens re-read) x remaining turns
                  (x 1.0: no cache discount yet). A re-read is assumed to come soon, so
                  the re-read text stays resident for the remaining turns (the cautious case).
A pointer's expected re-read is also one expected extra model call (the agent asks for
the file again), passed in by the planner as extra_calls = p_need_again.
Learned mode prices residency at the run's observed cache mix (context.resident_rate), and
an edit of earlier content pays a one-time cache break (cache_break_tokens).
  latency_ms    = linear estimate from config (model calls, tool runs, store lookups)

What the cache-aware version will change:
- predicted cache hits (from the cache belief) priced at cache_read_ratio;
- the cache-write premium on newly cached prefixes (Anthropic 1.25x / 2x);
- the invalidation cost of changing a prefix: every token after a change is re-sent
  uncached, so a reorder or an early edit is priced by what follows it;
- decode cost that grows with context length, and model-specific latency.
"""

from ..types import Candidate, CostBreakdown, PlanContext


# PLACEHOLDER: token-length pricing, no cache discount; the cache-aware version is listed above.
def price(candidate: Candidate, context: PlanContext, cfg: dict) -> CostBreakdown:
    p = candidate.payload
    if "meter_nu" in p:  # priced by costmodel/meter.py (worker placement): its whole cost
        return CostBreakdown(work=float(p["meter_nu"]),
                             latency_ms=latency(candidate, cfg, int(p["loop"]["calls"]),
                                                int(p["loop"]["output"])))
    r = context.rates
    output = cfg["cost_model"]["expected_output_tokens"]
    calls = p.get("model_calls", 0)            # model calls this plan causes
    extra_calls = p.get("extra_calls", 0)      # delegated calls (e.g. a fresh subagent)
    extra_input = p.get("extra_call_input_tokens", 0)
    # What is sent now is priced like residency: at the run's cache mix in learned mode
    # (resident_rate), else as uncached (1.0).
    prepare = (p.get("send_tokens", 0) * context.resident_rate
               + p.get("p_need_again", 0.0) * p.get("reread_tokens", 0)
               + extra_calls * extra_input)
    work = (calls + extra_calls) * output * r.output_ratio
    integrate = p.get("result_tokens", 0)
    leaves_behind = ((p.get("resident_tokens", 0)
                      + p.get("p_need_again", 0.0) * p.get("reread_tokens", 0))
                     * context.remaining_turns * context.resident_rate)
    # Editing earlier content breaks the cached prefix once: the tokens after it are sent
    # at the full price instead of the cache-read price on the next call.
    prepare += p.get("cache_break_tokens", 0) * (1 - r.cache_read_ratio)
    return CostBreakdown(prepare=float(prepare), work=float(work), integrate=float(integrate),
                         leaves_behind=float(leaves_behind),
                         latency_ms=latency(candidate, cfg, calls + extra_calls, output))


def latency(candidate: Candidate, cfg: dict, model_calls: int, output: int) -> float:
    lat = cfg["latency"]
    ms = model_calls * lat["model_base_ms"]
    if lat["ms_per_output_token"] is not None:
        ms += model_calls * output * lat["ms_per_output_token"]
    if candidate.payload.get("runs_tool"):
        ms += lat["tool_ms"]
    if candidate.payload.get("from_store"):
        ms += lat["store_lookup_ms"]
    return float(ms)
