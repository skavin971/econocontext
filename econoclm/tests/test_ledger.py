"""Gateway ledger: call numbers are assigned inside the insert's write transaction."""

import threading

from econoclm.core.gateway_ledger import Ledger


def test_concurrent_inserts_get_unique_call_numbers(tmp_path):
    path = tmp_path / "gateway.sqlite"
    Ledger(path).close()  # create the schema once
    start = threading.Barrier(12)
    got, errors = [], []

    def worker():
        ledger = Ledger(path)  # its own connection, like separate gateway threads/processes
        try:
            start.wait()
            got.append(ledger.insert("run-a", prompt_tokens=10, cost_usd=0.001))
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)
        finally:
            ledger.close()

    threads = [threading.Thread(target=worker) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert sorted(got) == list(range(12))
    rows = Ledger(path).calls("run-a")
    assert [r["call_no"] for r in rows] == list(range(12))


def test_call_numbers_are_per_run(tmp_path):
    ledger = Ledger(tmp_path / "g.sqlite")
    assert ledger.insert("a") == 0
    assert ledger.insert("b") == 0
    assert ledger.insert("a") == 1
    assert ledger.latest("a")["call_no"] == 1


def test_spend_sums(tmp_path):
    ledger = Ledger(tmp_path / "g.sqlite")
    ledger.insert("a", cost_usd=0.5)
    ledger.insert("b", cost_usd=0.25)
    assert ledger.total_spend() == 0.75
    assert ledger.run_spend("a") == 0.5
