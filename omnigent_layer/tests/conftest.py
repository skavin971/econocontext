"""Every test gets its own Agent DB and log directory; the repo's config is used."""

import pytest

import omnigent_layer
from omnigent_layer import gateway
from omnigent_layer import research


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(omnigent_layer, "DB_PATH", tmp_path / "agent.sqlite3")
    monkeypatch.setattr(gateway, "DB_PATH", tmp_path / "agent.sqlite3")
    monkeypatch.setattr(gateway, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(omnigent_layer, "_engines", {})
    monkeypatch.setattr(omnigent_layer, "CURRENT", tmp_path / "current_run")
    monkeypatch.setattr(research, "DB_PATH", tmp_path / "agent.sqlite3")
    monkeypatch.setattr(research, "RESEARCH_DB_PATH", tmp_path / "research.db")
    monkeypatch.setattr(research, "BINDINGS", tmp_path / "research-bindings")
    monkeypatch.setattr(research, "_cache", {})
    yield tmp_path
    for sink in research._cache.values():
        sink.close()
