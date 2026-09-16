def test_genie_home_env_is_honoured(tmp_path, monkeypatch):
    monkeypatch.setenv("GENIE_HOME", str(tmp_path))
    from genie.config import Settings

    s = Settings()
    assert s.genie_home == tmp_path
    assert s.db_path == tmp_path / "genie.db"


def test_mcp_token_from_env(monkeypatch):
    monkeypatch.setenv("GENIE_MCP_TOKEN", "tok-abc")
    from genie.config import Settings

    assert Settings().mcp_token == "tok-abc"


def test_mcp_token_defaults_empty(monkeypatch):
    monkeypatch.delenv("GENIE_MCP_TOKEN", raising=False)
    from genie.config import Settings

    assert Settings().mcp_token == ""
