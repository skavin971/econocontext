"""Every test gets its own Agent DB and log directory; the repo's config is used."""

import pytest

import omnigent_layer
from omnigent_layer import gateway


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(omnigent_layer, "DB_PATH", tmp_path / "agent.sqlite3")
    monkeypatch.setattr(gateway, "DB_PATH", tmp_path / "agent.sqlite3")
    monkeypatch.setattr(gateway, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(omnigent_layer, "_engines", {})
    return tmp_path
