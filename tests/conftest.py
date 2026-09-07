"""Shared fixtures. Every test gets an isolated GENIE_HOME + fresh SQLite DB."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def genie_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GENIE_HOME", str(tmp_path))
    from genie import config, db

    config.reset_settings_cache()
    db.reset_engine()
    db.init_db()
    yield tmp_path
    db.reset_engine()
    config.reset_settings_cache()


@pytest.fixture()
def client(genie_home):
    from genie.main import create_app

    with TestClient(create_app()) as c:
        yield c


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: full pipeline against FakeOpenRouter (slow-ish)")
