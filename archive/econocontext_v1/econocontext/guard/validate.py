"""Final checks on a rendered request before it goes back to the host.

Why it exists: a request that breaks message validity or drops pinned content is
worse than any cost saving; validation is the last line before the provider.
What it must never do: repair a request. It reports problems; the engine then
returns the host's own request instead.
"""

from ..assembler.zones import pairs_intact
from ..types import RenderedRequest, Segment


def problems(rendered: RenderedRequest, window: list[Segment],
             window_max_tokens: int | None) -> list[str]:
    found = []
    if not pairs_intact(rendered.segments):
        found.append("a tool call is not directly followed by its results")
    sent = {s.id for s in rendered.segments}
    missing = [s.id for s in window if s.pinned and s.id not in sent]
    if missing:
        found.append(f"pinned segments dropped: {missing[:3]}")
    if window_max_tokens is not None and rendered.manifest.token_estimate > window_max_tokens:
        found.append(f"{rendered.manifest.token_estimate} tokens exceed the window "
                     f"of {window_max_tokens}")
    return found
