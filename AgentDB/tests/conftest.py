"""Fixtures: a Postgres from pgserver (one per session, a fresh schema per test)."""

import pytest

from agent_db import PostgresAgentDB

RUN = "run-1"


@pytest.fixture(scope="session")
def pg_uri(tmp_path_factory):
    pgserver = pytest.importorskip("pgserver")
    server = pgserver.get_server(tmp_path_factory.mktemp("pgdata"))
    yield server.get_uri()
    server.cleanup()


@pytest.fixture
def db(pg_uri):
    import psycopg
    with psycopg.connect(pg_uri, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    store = PostgresAgentDB(pg_uri)
    store.start_run(RUN, "Fix the bug.")
    yield store
    store.close()
