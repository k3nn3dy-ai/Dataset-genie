"""/api/settings: JSON KV settings + secret management (values never returned)."""
from __future__ import annotations

import pytest

from genie import secrets


@pytest.fixture(autouse=True)
def fake_backend():
    store: dict[str, str] = {}
    secrets.set_backend_for_tests(store)
    yield store
    secrets.set_backend_for_tests(None)


def test_get_defaults(client):
    r = client.get("/api/settings/")
    assert r.status_code == 200
    body = r.json()
    assert body["budget_cap_usd"] == 15.0
    assert body["stop_at_pct"] == 90
    assert body["concurrency"] == 8
    assert body["prefer_prompt_caching"] is True
    assert body["allow_fallback_providers"] is True
    assert body["default_models"]["taxonomy"] == "anthropic/claude-sonnet-4"
    assert body["default_models"]["judge"] == "openai/gpt-4o"
    assert body["default_models"]["prompts"] == "openai/gpt-4o-mini"
    assert body["default_models"]["responses"] == "anthropic/claude-sonnet-4"
    assert body["default_models"]["embeddings"] == "openai/text-embedding-3-small"


def test_put_merges_and_persists(client):
    r = client.put("/api/settings/", json={"budget_cap_usd": 3.5, "default_models": {"judge": "x/y"}})
    assert r.status_code == 200
    body = r.json()
    assert body["budget_cap_usd"] == 3.5
    assert body["default_models"]["judge"] == "x/y"
    assert body["default_models"]["taxonomy"] == "anthropic/claude-sonnet-4"  # untouched
    assert body["concurrency"] == 8
    r2 = client.get("/api/settings/")
    assert r2.json()["budget_cap_usd"] == 3.5
    assert r2.json()["default_models"]["judge"] == "x/y"


def test_put_rejects_secret_like_keys(client):
    r = client.put("/api/settings/", json={"openrouter_api_key": "sk-or-xxx"})
    assert r.status_code == 400


def test_secrets_flow_never_returns_value(client, fake_backend):
    r = client.get("/api/settings/secrets/status")
    assert r.json() == {"openrouter": False, "huggingface": False}
    r = client.put("/api/settings/secrets", json={"name": "openrouter", "value": "sk-or-secret"})
    assert r.status_code == 200
    assert "sk-or-secret" not in r.text
    assert r.json() == {"openrouter": True, "huggingface": False}
    assert fake_backend == {"openrouter": "sk-or-secret"}
    r = client.get("/api/settings/")
    assert "sk-or-secret" not in r.text
    r = client.delete("/api/settings/secrets/openrouter")
    assert r.status_code == 200
    assert r.json() == {"openrouter": False, "huggingface": False}


def test_secrets_bad_name(client):
    r = client.put("/api/settings/secrets", json={"name": "aws", "value": "x"})
    assert r.status_code == 400
    r = client.delete("/api/settings/secrets/aws")
    assert r.status_code == 400


# ----------------------------------------------------------------------------- review fixes
@pytest.mark.parametrize("patch", [
    {"provider_order": "openai"},            # str, not list
    {"budget_cap_usd": "lots"},              # not a number
    {"budget_cap_usd": -1},
    {"stop_at_pct": 150},
    {"concurrency": 0},
    {"default_models": {"judge": 5}},
    {"default_models": "openai/gpt-4o"},
    {"not_a_setting": True},
    {"prefer_prompt_caching": "yes please"},
])
def test_put_rejects_invalid_values(client, patch):
    r = client.put("/api/settings/", json=patch)
    assert r.status_code == 400, r.text
    # nothing persisted
    assert client.get("/api/settings/").json()["budget_cap_usd"] == 15.0


@pytest.mark.parametrize("patch", [
    {"provider_order": ["sk-or-v1-0123456789abcdef0123456789abcdef"]},
    {"default_models": {"judge": "hf_abcdefghijklmnopqrstuvwxyz0123"}},
])
def test_put_rejects_secret_like_values(client, patch):
    r = client.put("/api/settings/", json=patch)
    assert r.status_code == 400
    assert "sk-or" not in client.get("/api/settings/").text
    assert "hf_abc" not in client.get("/api/settings/").text


def test_put_valid_values_are_coerced_and_typed(client):
    r = client.put("/api/settings/", json={"provider_order": ["OpenAI", "Anthropic"], "concurrency": 4,
                                            "stop_at_pct": 80, "budget_cap_usd": 2})
    assert r.status_code == 200
    body = r.json()
    assert body["provider_order"] == ["OpenAI", "Anthropic"]
    assert body["concurrency"] == 4 and body["stop_at_pct"] == 80 and body["budget_cap_usd"] == 2.0
