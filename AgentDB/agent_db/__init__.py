"""Agent DBs built to EconoContext's AGENT_DB_CONTRACT.md: one on Postgres, one on Neo4j.

Part A (the data) is schema.sql for Postgres and the constraints in neo4j_store.py for
the graph. Part B (the API) is the same set of operations on both. The SQLite store in
econocontext/store/db.py is the reference they are checked against.
"""

from .postgres import PostgresAgentDB, PostgresBlobStore, Snapshot, read_paths

__all__ = ["PostgresAgentDB", "PostgresBlobStore", "Snapshot", "read_paths", "Neo4jAgentDB"]


def __getattr__(name):
    if name == "Neo4jAgentDB":  # the driver is optional; import on first use
        from .neo4j_store import Neo4jAgentDB
        return Neo4jAgentDB
    raise AttributeError(name)
