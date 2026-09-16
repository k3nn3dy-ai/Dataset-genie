from fastapi.testclient import TestClient

from genie.mcp.auth import ensure_env_mcp_token

MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}

INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "0"},
    },
}


def test_ensure_env_mcp_token_writes_when_missing(tmp_path):
    path = tmp_path / ".env"
    path.write_text("GENIE_PORT=8765\n", encoding="utf-8")
    token, created = ensure_env_mcp_token(path)
    assert created is True
    assert len(token) >= 32
    text = path.read_text(encoding="utf-8")
    assert f"GENIE_MCP_TOKEN={token}" in text
    assert "GENIE_PORT=8765" in text


def test_ensure_env_mcp_token_fills_empty(tmp_path):
    path = tmp_path / ".env"
    path.write_text("GENIE_MCP_TOKEN=\nOPENROUTER_API_KEY=x\n", encoding="utf-8")
    token, created = ensure_env_mcp_token(path)
    assert created is True
    assert token
    lines = path.read_text(encoding="utf-8").splitlines()
    assert f"GENIE_MCP_TOKEN={token}" in lines
    assert "OPENROUTER_API_KEY=x" in lines


def test_ensure_env_mcp_token_leaves_existing(tmp_path):
    path = tmp_path / ".env"
    path.write_text("GENIE_MCP_TOKEN=already-set-token-value\n", encoding="utf-8")
    token, created = ensure_env_mcp_token(path)
    assert created is False
    assert token == "already-set-token-value"
    assert path.read_text(encoding="utf-8") == "GENIE_MCP_TOKEN=already-set-token-value\n"


def test_mcp_disabled_without_token(client):
    r = client.post("/mcp", json=INIT, headers=MCP_HEADERS)
    assert r.status_code == 503
    assert r.json()["detail"] == "MCP disabled; set GENIE_MCP_TOKEN"
    assert client.get("/api/health").status_code == 200


def test_mcp_401_missing_and_wrong_token(genie_home, monkeypatch):
    monkeypatch.setenv("GENIE_MCP_TOKEN", "correct-token-value-here")
    from genie import config, main

    config.reset_settings_cache()
    with TestClient(main.create_app()) as c:
        assert c.post("/mcp", json=INIT, headers=MCP_HEADERS).status_code == 401
        bad = {**MCP_HEADERS, "Authorization": "Bearer wrong-token-value-here"}
        assert c.post("/mcp", json=INIT, headers=bad).status_code == 401
        short = {**MCP_HEADERS, "Authorization": "Bearer x"}
        r = c.post("/mcp", json=INIT, headers=short)
        assert r.status_code == 401


def test_mcp_initialize_with_token(genie_home, monkeypatch):
    monkeypatch.setenv("GENIE_MCP_TOKEN", "correct-token-value-here")
    from genie import config, main

    config.reset_settings_cache()
    with TestClient(main.create_app()) as c:
        headers = {**MCP_HEADERS, "Authorization": "Bearer correct-token-value-here"}
        r = c.post("/mcp", json=INIT, headers=headers)
        assert r.status_code == 200
        assert "index.html" not in r.text
        assert "jsonrpc" in r.text or "Dataset Genie" in r.text or "protocolVersion" in r.text
