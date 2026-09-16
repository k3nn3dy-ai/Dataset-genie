from pathlib import Path

from genie.mcp.auth import ensure_env_mcp_token


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
