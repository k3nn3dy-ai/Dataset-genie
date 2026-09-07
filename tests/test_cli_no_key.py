"""CLI commands that need OpenRouter must fail with the friendly MissingApiKey message, not a traceback."""
from __future__ import annotations

from typer.testing import CliRunner


def test_models_without_key_gives_friendly_error(genie_home, monkeypatch):
    import genie.secrets as secrets
    from genie.cli import app

    monkeypatch.setattr(secrets, "get_secret", lambda name: None, raising=True)
    result = CliRunner().invoke(app, ["models"])
    out = (result.output or "") + str(result.exception or "")
    assert result.exit_code != 0
    assert "TypeError" not in out and "Traceback" not in out, out
    assert "No OpenRouter API key" in out, out
