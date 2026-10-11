"""pytest plugin: run EconoContext's own unit tests on another Agent DB.

    AGENT_DB_BACKEND=postgres|neo4j  pytest tests/unit -p econocontext_backend

Replaces the AgentDB class the engine and the tests construct with a factory for the
chosen backend. Each distinct db path the tests ask for gets its own isolated store:
a schema of its own on Postgres, a wiped graph on Neo4j (tests run one at a time).
"""

import hashlib
import os
import tempfile

BACKEND = os.environ.get("AGENT_DB_BACKEND", "sqlite")
_seen: set[str] = set()
_server = None


def _postgres_factory():
    global _server
    import pgserver
    import psycopg
    from agent_db import PostgresAgentDB
    _server = pgserver.get_server(os.path.join(tempfile.mkdtemp(), "pgdata"))
    uri = _server.get_uri()

    def factory(path, blob_min_bytes=4096):
        schema = "s_" + hashlib.sha256(str(path).encode()).hexdigest()[:12]
        with psycopg.connect(uri, autocommit=True) as conn:
            conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        return PostgresAgentDB(f"{uri}&options=-c%20search_path%3D{schema}", blob_min_bytes)
    return factory


def _neo4j_factory():
    from agent_db.neo4j_store import Neo4jAgentDB
    def factory(path, blob_min_bytes=4096):
        db = Neo4jAgentDB(blob_min_bytes=blob_min_bytes)  # NEO4J_URI / NEO4J_AUTH from the environment
        if str(path) not in _seen:
            db.wipe()
            _seen.add(str(path))
        return db
    return factory


def pytest_configure(config):
    if BACKEND == "sqlite":
        return
    factory = {"postgres": _postgres_factory, "neo4j": _neo4j_factory}[BACKEND]()
    import econocontext.engine as engine
    import econocontext.store.db as store
    engine.AgentDB = factory
    store.AgentDB = factory


def pytest_unconfigure(config):
    if _server is not None:
        _server.cleanup()
