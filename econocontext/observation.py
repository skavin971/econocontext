"""Optional, one-way observations. No agent behavior may depend on a sink's result."""

import logging
from contextlib import contextmanager
from contextvars import ContextVar

log = logging.getLogger(__name__)
_current = ContextVar("research_observation", default=(None, {}))


def observe(sink, kind: str, payload, **links) -> None:
    if sink is None:
        return
    try:
        _, inherited = _current.get()
        sink.emit(kind, payload, **{**inherited, **links})
    except Exception:
        # A custom sink must be just as optional as the built-in SQLite recorder.
        log.exception("Research observation failed; execution continues")


@contextmanager
def observing(sink, **links):
    _, inherited = _current.get()
    token = _current.set((sink, {**inherited, **links}))
    try:
        yield
    finally:
        _current.reset(token)


def observe_current(kind: str, payload, **links) -> None:
    sink, _ = _current.get()
    observe(sink, kind, payload, **links)


def current_observer():
    return _current.get()[0]
