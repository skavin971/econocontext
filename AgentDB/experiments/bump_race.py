"""Do concurrent bumps of one file lose updates?

Several writers, each with its own connection (as the gateway process and the Omnigent
process each have their own), bump the same source many times. Every bump should add
one, so the final version should equal writers x bumps. Anything less is a lost update:
a file changed but its version did not move, so a result read in between stays 'valid'.
"""

import sys
import tempfile
import threading
from pathlib import Path

WRITERS, BUMPS = 4, 50
RUN, SRC = "race:econo:i1", "src/a.py"


def trial(name, make):
    stores = [make() for _ in range(WRITERS)]
    stores[0].start_run(RUN, "t", "i1", "econo", "observe", "m", 1.0, "fp")
    errors = []

    def work(db):
        for _ in range(BUMPS):
            try:
                db.bump(RUN, [SRC])
            except Exception as exc:  # noqa: BLE001
                errors.append(type(exc).__name__)

    threads = [threading.Thread(target=work, args=(db,)) for db in stores]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    final = int(stores[0].current_versions(RUN).get(SRC, "0"))
    expected = WRITERS * BUMPS
    lost = expected - len(errors) - final
    print(f"{name:<9} expected {expected}  got {final}  errors {len(errors)}  "
          f"lost updates {lost}" + (f"  ({sorted(set(errors))})" if errors else ""))


def sqlite():
    from econocontext.store.db import AgentDB
    path = Path(tempfile.mkdtemp()) / "db.sqlite3"
    return lambda: AgentDB(path)


def postgres():
    import pgserver
    from agent_db import PostgresAgentDB
    server = pgserver.get_server(Path(tempfile.mkdtemp()) / "pg")
    uri = server.get_uri()
    return lambda: PostgresAgentDB(uri)


def neo4j():
    from agent_db.neo4j_store import Neo4jAgentDB
    Neo4jAgentDB().wipe()
    return lambda: Neo4jAgentDB()


if __name__ == "__main__":
    for name in sys.argv[1:] or ["sqlite", "postgres", "neo4j"]:
        for attempt in range(3):
            trial(f"{name}#{attempt + 1}", {"sqlite": sqlite, "postgres": postgres,
                                            "neo4j": neo4j}[name]())
