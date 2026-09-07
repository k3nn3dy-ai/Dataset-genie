def test_genie_home_env_is_honoured(tmp_path, monkeypatch):
    monkeypatch.setenv("GENIE_HOME", str(tmp_path))
    from genie.config import Settings

    s = Settings()
    assert s.genie_home == tmp_path
    assert s.db_path == tmp_path / "genie.db"
