"""Fixtures: a Postgres from pgserver (one per session, a fresh schema per test) and the
SQLite reference from econocontext, so the contract's shared behaviour runs on both."""

import json
import os
import time

import pytest

from agent_db import PostgresAgentDB
from agent_db.records import AgentNode, SegmentKind, make_segment

try:  # the SQLite reference, when an EconoContext with store/db.py (branch econo-jev) is installed
    from econocontext.store.db import AgentDB as SqliteAgentDB
except ImportError:  # pragma: no cover
    SqliteAgentDB = None

RUN = "lbl:econo:inst-1"
ROOT = f"{RUN}:root"
WORKER = f"{RUN}:worker:aaaa1111"


@pytest.fixture(scope="session")
def pg_uri(tmp_path_factory):
    pgserver = pytest.importorskip("pgserver")
    server = pgserver.get_server(tmp_path_factory.mktemp("pgdata"))
    yield server.get_uri()
    server.cleanup()


@pytest.fixture
def postgres(pg_uri):
    import psycopg
    with psycopg.connect(pg_uri, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    db = PostgresAgentDB(pg_uri)
    yield db
    db.close()


@pytest.fixture
def sqlite(tmp_path):
    if SqliteAgentDB is None:
        pytest.skip("the SQLite reference needs EconoContext (branch econo-jev) installed")
    return SqliteAgentDB(tmp_path / "db.sqlite3")


NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_AUTH = tuple(os.environ["NEO4J_AUTH"].split(":", 1)) if os.environ.get("NEO4J_AUTH") else None


@pytest.fixture(scope="session")
def neo4j_available():
    neo4j = pytest.importorskip("neo4j")
    try:
        drv = neo4j.GraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)
        drv.verify_connectivity()
        drv.close()
    except Exception as exc:  # no server: skip, do not fail
        pytest.skip(f"Neo4j not reachable at {NEO4J_URI}: {exc}")
    return True


@pytest.fixture
def neo4j(neo4j_available):
    from agent_db.neo4j_store import Neo4jAgentDB
    db = Neo4jAgentDB(NEO4J_URI, NEO4J_AUTH)
    db.wipe()
    yield db
    db.close()


@pytest.fixture
def connect(request):
    """Open another connection to the same store as the `db` fixture's backend."""
    def make(db):
        name = db.__class__.__name__
        if name.startswith("Postgres"):
            return PostgresAgentDB(request.getfixturevalue("pg_uri"))
        if name.startswith("Neo4j"):
            from agent_db.neo4j_store import Neo4jAgentDB
            return Neo4jAgentDB(NEO4J_URI, NEO4J_AUTH)
        return SqliteAgentDB(db.conn.execute("PRAGMA database_list").fetchone()[2])
    return make


def with_run(store):
    store.start_run(RUN, "test", "inst-1", "econo", "observe", "m", 1.0, "fp")
    for agent in (ROOT, WORKER):
        store.upsert_agent(RUN, AgentNode(agent, None if agent == ROOT else ROOT,
                                          None if agent == ROOT else "worker"))
    return store


@pytest.fixture(params=["sqlite", "postgres", "neo4j"])
def db(request):
    """Any backend, with one run and two agents registered: the reference's behaviour."""
    return with_run(request.getfixturevalue(request.param))


@pytest.fixture(params=["postgres", "neo4j"])
def contract_db(request):
    """The backends built to the contract: the operations the reference lacks."""
    return with_run(request.getfixturevalue(request.param))


def seg(agent, native, kind, text, role="tool", **kw):
    return make_segment(RUN, agent, native, kind, text, role=role, **kw)


def J(value):
    """JSON columns: the reference returns text, Postgres returns decoded values."""
    return json.loads(value) if isinstance(value, str) else value


def q(db, sql: str, args=()):
    """Run plain SQL on the SQL backends ('?' becomes '%s' on Postgres). On Neo4j the
    caller passes a Cypher alternative through `cypher=`; see `peek`."""
    if db.__class__.__name__.startswith("Postgres"):
        sql = sql.replace("?", "%s")
    return db.rows(sql, args)


def peek(db, sql: str, cypher: str, args=()):
    """One row from either query language, whichever the backend speaks."""
    if db.__class__.__name__.startswith("Neo4j"):
        return db.rows(cypher, {"a": args[0]} if args else {})[0]
    return q(db, sql, args)[0]


def tick():
    time.sleep(0.002)  # keep created_at strictly increasing within a test


def reads(path: str) -> str:
    return "reading\n" + json.dumps([{"id": "c", "name": "sys_os_read",
                                      "args": json.dumps({"path": path})}])


def conversation(agent=ROOT):
    return [
        seg(agent, "sys", SegmentKind.SYSTEM, "You are a coding agent.", role="system"),
        seg(agent, "task", SegmentKind.TASK, "Fix the bug in src/app.py parse_items", role="user"),
        seg(agent, "ai1", SegmentKind.TOOL_CALL, 'read_file {"file_path": "/testbed/src/app.py"}',
            role="assistant", pair_id="c1"),
        seg(agent, "c1", SegmentKind.TOOL_RESULT, "def parse_items(x):\n    return x\n", role="tool",
            pair_id="c1", source="/testbed/src/app.py"),
    ]
