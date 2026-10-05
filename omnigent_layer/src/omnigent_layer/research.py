"""Research sink discovery across bench, gateway, and tool-policy processes.

Bindings are tiny sidecar files outside both databases. They only select a sink;
their contents are never used for agent configuration or operational decisions.
"""

import hashlib
import json
import logging
import os
import threading
import uuid
from pathlib import Path

from econocontext.observation import observe
from econocontext.research import ResearchRecorder
from econocontext.research.recorder import record_failure

from . import DB_PATH, HOME

BINDINGS = HOME / "data" / "research-bindings"
_cache = {}
_lock = threading.RLock()
log = logging.getLogger(__name__)


def _path(kind, key):
    digest = hashlib.sha256(str(key).encode()).hexdigest()
    return BINDINGS / f"{kind}-{digest}.json"


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(data))
    os.replace(temporary, path)


def start(run_id, database, metadata, workdir=None):
    """Explicit bench opt-in. Fail before a paid run if configuration is invalid."""
    sink = ResearchRecorder.start(database, run_id, metadata, operational_path=DB_PATH)
    if sink.failed:
        sink.close()
        raise RuntimeError("Could not initialize research recording")
    descriptor = dict(database=str(sink.path), trace_id=sink.run_id, runtime_run_id=run_id)
    with _lock:
        _cache[(str(sink.path), sink.run_id)] = sink
    _write(_path("run", run_id), descriptor)
    if workdir:
        _write(_path("workspace", Path(workdir).resolve()), descriptor)
    return sink


def stop(run_id, sink, workdir=None):
    """Stop discovery of this trace without removing another invocation's binding."""
    if sink is None:
        return
    for path in [_path("run", run_id)] + ([_path("workspace", Path(workdir).resolve())] if workdir else []):
        try:
            if json.loads(path.read_text())["trace_id"] == sink.run_id:
                path.unlink()
        except (OSError, ValueError, KeyError):
            log.warning("Could not remove research binding %s", path)
    with _lock:
        _cache.pop((str(sink.path), sink.run_id), None)
    try:
        sink.close()
    except Exception:
        log.exception("Could not close research recorder")


def disable(run_id, workdir=None):
    """Explicitly disabled invocation: clear stale descriptors left by a killed run."""
    paths = [_path("run", run_id)]
    if workdir:
        paths.append(_path("workspace", Path(workdir).resolve()))
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            log.warning("Could not remove stale research binding %s", path)


def _get(path):
    descriptor = None
    try:
        if not path.exists():
            return None
        descriptor = json.loads(path.read_text())
        key = (descriptor["database"], descriptor["trace_id"])
        with _lock:
            if key not in _cache:
                _cache[key] = ResearchRecorder(*key, operational_path=DB_PATH)
            return _cache[key]
    except Exception as exc:
        log.exception("Could not attach research recorder; execution continues")
        if descriptor and "database" in descriptor and "trace_id" in descriptor:
            record_failure(descriptor["database"], descriptor["trace_id"], "attach", exc)
        # Report to an already attached bench recorder when possible; never configure
        # the operational engine from this sidecar or read the research DB for it.
        return None


def recorder_for(run_id):
    return _get(_path("run", run_id))


def recorder_for_workspace(workdir):
    return _get(_path("workspace", Path(workdir).resolve()))


def artifact(sink, name, content, media_type="application/json"):
    observe(sink, "artifact", dict(name=name, content=content, media_type=media_type), source="bench")
