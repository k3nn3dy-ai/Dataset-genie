"""/api/models: catalogue search from cache, graceful without an API key, refresh."""
from __future__ import annotations

import time

import pytest

from genie import secrets
from genie.db import session_scope
from genie.models import CatalogueCache
from test_openrouter import CATALOGUE, FakeServer


@pytest.fixture(autouse=True)
def fake_secrets():
    store: dict[str, str] = {}
    secrets.set_backend_for_tests(store)
    yield store
    secrets.set_backend_for_tests(None)


@pytest.fixture()
def srv(genie_home, monkeypatch) -> FakeServer:
    s = FakeServer()
    s.set("GET", "/api/v1/models", 200, CATALOGUE)
    fake = s.client()
    monkeypatch.setattr("genie.providers.openrouter.get_client", lambda **kw: fake)
    return s


def test_no_key_and_no_cache_returns_empty_with_warning(client):
    r = client.get("/api/models/")
    assert r.status_code == 200
    assert r.json() == []
    assert "api key" in r.headers["x-genie-warning"].lower()


def test_no_key_uses_db_cache(client, genie_home):
    with session_scope() as s:
        s.add(CatalogueCache(id=1, fetched_at=time.time(), payload=CATALOGUE["data"]))
    r = client.get("/api/models/?q=llama")
    assert r.status_code == 200
    assert [m["id"] for m in r.json()] == ["meta-llama/llama-3.1-8b-instruct"]
    assert "x-genie-warning" in r.headers
    r = client.get("/api/models/?q=OPENAI")
    assert {m["id"] for m in r.json()} == {
        "openai/gpt-4o", "openai/gpt-5.6-sol", "openai/o-next", "openai/text-embedding-3-small",
    }


def test_with_key_fetches_and_searches(client, srv, fake_secrets):
    fake_secrets["openrouter"] = "sk-or-x"
    r = client.get("/api/models/")
    assert r.status_code == 200 and len(r.json()) == len(CATALOGUE["data"])
    assert "x-genie-warning" not in r.headers
    m = next(x for x in r.json() if x["id"] == "openai/gpt-4o")
    assert m["prompt_price_per_m"] == pytest.approx(2.5) and m["supports_json_schema"] is True
    assert len(srv.calls("/api/v1/models")) == 1
    r = client.get("/api/models/?q=plain")
    assert [x["id"] for x in r.json()] == ["some/plain-model"]
    assert len(srv.calls("/api/v1/models")) == 1  # served from memory/DB cache


def test_refresh(client, srv, fake_secrets):
    fake_secrets["openrouter"] = "sk-or-x"
    client.get("/api/models/")
    r = client.get("/api/models/refresh")
    assert r.status_code == 200 and r.json()["count"] == len(CATALOGUE["data"])
    assert len(srv.calls("/api/v1/models")) == 2


def test_refresh_without_key_is_400(client):
    r = client.get("/api/models/refresh")
    assert r.status_code == 400 and "api key" in r.json()["detail"].lower()


def test_network_failure_falls_back_to_cache_with_warning(client, srv, fake_secrets):
    fake_secrets["openrouter"] = "sk-or-x"
    with session_scope() as s:
        s.add(CatalogueCache(id=1, fetched_at=time.time() - 48 * 3600, payload=CATALOGUE["data"][:2]))
    srv.set("GET", "/api/v1/models", 500, {"error": {"message": "down"}})
    r = client.get("/api/models/")
    assert r.status_code == 200 and len(r.json()) == 2
    assert "x-genie-warning" in r.headers
